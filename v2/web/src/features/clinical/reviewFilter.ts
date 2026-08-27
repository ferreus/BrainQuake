import {
  butterLowpassSos,
  filtfiltInto,
  iirnotch,
  lfilterInto,
  lfilterZi,
  mainsHarmonics,
  makeScratch,
  sosfiltfiltInto,
} from "../../lib/dsp";

/** Client-side port of app/sigproc/filters.py's filter_for_review, so the
 * clinical view can re-filter a buffered window without a round trip -- which
 * is what makes changing TC / high cut / mains / CAR instant.
 *
 * Lives under features/clinical/ on purpose: the analysis path filters
 * server-side and must not pick this up. scripts/verify-review-filter.ts diffs
 * it against the live server, which is what keeps the two in step. */

export type ReviewReference = "car" | "none";

export interface ReviewFilterOptions {
  /** Seconds. null = low cut off. */
  tc: number | null;
  /** Hz. null = high cut off. */
  hicut: number | null;
  mainsFreq: number;
  reference: ReviewReference;
}

/** Fraction of Nyquist a high cut has to stay below, matching filters._NYQ_MARGIN. */
const NYQ_MARGIN = 0.99;
/** The legacy app notched 50/100/150 only; filters._reference_and_notch keeps
 * that reach rather than sweeping to Nyquist. */
const NOTCH_HARMONIC_REACH = 3.5;

/** Nihon Kohden's TC filter: a causal one-pole RC high-pass, -6 dB/oct, corner
 * 1/(2*pi*tc) Hz. Started in steady state -- from rest, a contact's DC offset
 * would answer with a full-amplitude step decaying over tc. */
function rcHighpassInto(x: Float64Array, fs: number, tc: number, scratch: Float64Array): void {
  const a = tc / (tc + 1 / fs);
  const b = [a, -a];
  const ac = [1, -a];
  const zi = lfilterZi(b, ac).map((z) => z * x[0]);
  const out = scratch.subarray(0, x.length);
  lfilterInto(b, ac, x, zi, false, out);
  x.set(out);
}

/** Common-average reference across the rows, in place. Skipped below two rows:
 * the average of one channel is that channel, so subtracting it returns zero. */
function applyCarInPlace(rows: Float64Array[]): void {
  if (rows.length < 2) return;
  const n = rows[0].length;
  for (let i = 0; i < n; i++) {
    let sum = 0;
    for (let c = 0; c < rows.length; c++) sum += rows[c][i];
    const mean = sum / rows.length;
    for (let c = 0; c < rows.length; c++) rows[c][i] -= mean;
  }
}

/** Reference, mains notch, causal TC high-pass, then an independent high cut --
 * the order a Nihon Kohden review screen applies them.
 *
 * Both filters off is a legal state: the caller still gets referenced, notched
 * traces, which is why the view has no single "filtering off". mainsFreq <= 0
 * disables the notch. Bipolar data arrives already referenced and must pass
 * reference "none", matching edf.py's `applied_reference = "none" if bipolar`. */
export function filterForReview(
  data: Float64Array[],
  fs: number,
  { tc, hicut, mainsFreq, reference }: ReviewFilterOptions,
): Float64Array[] {
  if (tc != null && tc <= 0) throw new Error(`tc must be > 0 seconds, got ${tc}`);
  if (hicut != null && hicut <= 0) throw new Error(`hicut must be > 0 Hz, got ${hicut}`);
  if (data.length === 0) return data;

  // Everything below runs in place over one copy, sharing two scratch buffers:
  // a fresh array per filter per channel was ~500 MB of garbage per chain and
  // the GC, not the arithmetic, was the cost.
  const rows = data.map((r) => Float64Array.from(r));
  const scratch = makeScratch(rows[0].length);

  if (reference === "car") applyCarInPlace(rows);

  for (const nf of mainsHarmonics(mainsFreq, fs, mainsFreq * NOTCH_HARMONIC_REACH)) {
    const { b, a } = iirnotch(nf / (fs / 2), 30);
    for (const r of rows) filtfiltInto(b, a, r, scratch);
  }

  if (tc != null) for (const r of rows) rcHighpassInto(r, fs, tc, scratch.p);

  if (hicut != null && hicut < NYQ_MARGIN * (fs / 2)) {
    // At or above the usable Nyquist the high cut is left off, not clamped, as
    // show_edf.py does -- it would be transparent anyway.
    const sos = butterLowpassSos(4, hicut / (fs / 2));
    for (const r of rows) sosfiltfiltInto(sos, r, scratch);
  }

  return rows;
}
