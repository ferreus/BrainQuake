/** Ports of the scipy.signal calls app/sigproc/filters.py makes, so the clinical
 * review filter can run on the client over a buffered window instead of costing
 * a round trip per pan.
 *
 * These reproduce scipy's *defaults* (filtfilt's odd extension and padlen, the
 * lfilter_zi warm start, zpk2sos's gain placement), not just the maths -- the
 * output has to match filter_for_review sample for sample, and
 * scripts/verify-review-filter.ts is what proves it does.
 *
 * Only what the review chain needs: no bandpass, no clamp_band. The analysis
 * path still filters server-side and imports none of this. */

export type Biquad = [number, number, number, number, number, number];

/** Solves a small dense system by Gaussian elimination with partial pivoting.
 * Sized for lfilterZi, where n is the filter order (<= 2 here). */
function solve(a: number[][], b: number[]): number[] {
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let pivot = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(m[r][col]) > Math.abs(m[pivot][col])) pivot = r;
    [m[col], m[pivot]] = [m[pivot], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => row[n] / row[i]);
}

/** scipy.signal.lfilter_zi: the steady-state initial condition, i.e. the state
 * for which a constant input holds the output at that same constant. */
export function lfilterZi(b: number[], a: number[]): number[] {
  const n = Math.max(a.length, b.length);
  const ap = [...a, ...Array(n - a.length).fill(0)];
  const bp = [...b, ...Array(n - b.length).fill(0)];
  const a0 = ap[0];
  for (let i = 0; i < n; i++) {
    ap[i] /= a0;
    bp[i] /= a0;
  }
  // I - companion(a).T
  const size = n - 1;
  const m: number[][] = Array.from({ length: size }, (_, r) =>
    Array.from({ length: size }, (_, c) => (r === c ? 1 : 0) - (c === 0 ? -ap[r + 1] : r === c - 1 ? 1 : 0)),
  );
  const rhs = Array.from({ length: size }, (_, i) => bp[i + 1] - ap[i + 1] * bp[0]);
  return solve(m, rhs);
}

/** scipy.signal.lfilter, transposed direct form II.
 *
 * `reverse` walks the input from the end and still writes each output at its own
 * index, which is exactly filtfilt's backward pass without the two full-length
 * copies a reverse-filter-reverse would allocate.
 *
 * The 2- and 3-tap cases are unrolled onto scalar state: every filter in the
 * review chain is one of those (RC high-pass, mains notch), and the generic
 * loop's array indexing dominated the profile. */
export function lfilterInto(
  b: number[],
  a: number[],
  x: Float64Array,
  zi: number[] | undefined,
  reverse: boolean,
  y: Float64Array,
): void {
  const n = Math.max(a.length, b.length);
  const ap = [...a, ...Array(Math.max(0, n - a.length)).fill(0)];
  const bp = [...b, ...Array(Math.max(0, n - b.length)).fill(0)];
  const a0 = ap[0];
  for (let i = 0; i < n; i++) {
    ap[i] /= a0;
    bp[i] /= a0;
  }
  const len = x.length;
  const step = reverse ? -1 : 1;
  const first = reverse ? len - 1 : 0;

  if (n === 3) {
    const [b0, b1, b2] = bp;
    const a1 = ap[1];
    const a2 = ap[2];
    let z0 = zi ? zi[0] : 0;
    let z1 = zi ? zi[1] : 0;
    for (let k = 0, i = first; k < len; k++, i += step) {
      const xi = x[i];
      const yi = b0 * xi + z0;
      z0 = b1 * xi - a1 * yi + z1;
      z1 = b2 * xi - a2 * yi;
      y[i] = yi;
    }
    return;
  }

  if (n === 2) {
    const [b0, b1] = bp;
    const a1 = ap[1];
    let z0 = zi ? zi[0] : 0;
    for (let k = 0, i = first; k < len; k++, i += step) {
      const xi = x[i];
      const yi = b0 * xi + z0;
      z0 = b1 * xi - a1 * yi;
      y[i] = yi;
    }
    return;
  }

  const z = zi ? [...zi, 0] : new Array(n).fill(0);
  for (let k = 0, i = first; k < len; k++, i += step) {
    const xi = x[i];
    const yi = bp[0] * xi + z[0];
    for (let j = 1; j < n - 1; j++) z[j - 1] = bp[j] * xi + z[j] - ap[j] * yi;
    if (n > 1) z[n - 2] = bp[n - 1] * xi - ap[n - 1] * yi;
    y[i] = yi;
  }
}

/** Allocating wrapper around lfilterInto. */
export function lfilter(b: number[], a: number[], x: Float64Array, zi?: number[], reverse = false): Float64Array {
  const y = new Float64Array(x.length);
  lfilterInto(b, a, x, zi, reverse, y);
  return y;
}

const scaled = (v: number[], s: number) => v.map((z) => z * s);

/** Largest padlen any filter in the review chain asks for: sosfiltfilt over two
 * Butterworth sections. Sizes the scratch buffers. */
const MAX_EDGE = 15;

export interface Scratch {
  p: Float64Array;
  q: Float64Array;
}

/** Two reusable buffers for the in-place filters below. Allocating per call
 * instead cost ~500 MB of garbage for one 20-channel chain, and the GC dominated
 * the profile. */
export function makeScratch(sampleCount: number): Scratch {
  const n = sampleCount + 2 * MAX_EDGE;
  return { p: new Float64Array(n), q: new Float64Array(n) };
}

function oddExtInto(x: Float64Array, edge: number, out: Float64Array): void {
  for (let i = 0; i < edge; i++) out[i] = 2 * x[0] - x[edge - i];
  out.set(x, edge);
  const last = x.length - 1;
  for (let i = 0; i < edge; i++) out[edge + x.length + i] = 2 * x[last] - x[last - 1 - i];
}

/** scipy.signal.filtfilt (method="pad", padtype="odd",
 * padlen = 3 * max(len(a), len(b))), writing the result back into `x`. */
export function filtfiltInto(b: number[], a: number[], x: Float64Array, sc: Scratch): void {
  const edge = 3 * Math.max(a.length, b.length);
  if (x.length <= edge) throw new Error(`filtfilt: signal of ${x.length} is too short for padlen ${edge}`);
  const ext = x.length + 2 * edge;
  const zi = lfilterZi(b, a);
  const p = sc.p.subarray(0, ext);
  const q = sc.q.subarray(0, ext);
  oddExtInto(x, edge, p);
  lfilterInto(b, a, p, scaled(zi, p[0]), false, q);
  // The backward pass warms from the forward pass's last sample -- what
  // reversing the array would have put first.
  lfilterInto(b, a, q, scaled(zi, q[ext - 1]), true, p);
  x.set(p.subarray(edge, edge + x.length));
}

/** Allocating wrapper, for callers outside the hot chain (benchmarks, checks). */
export function filtfilt(b: number[], a: number[], x: Float64Array): Float64Array {
  const y = Float64Array.from(x);
  filtfiltInto(b, a, y, makeScratch(x.length));
  return y;
}

/** scipy.signal.sosfilt: a cascade of biquads, each transposed direct form II.
 * Ping-pongs between `src` and `dst`; returns whichever now holds the result. */
function sosfiltPingPong(
  sos: Biquad[],
  src: Float64Array,
  zi: number[][] | undefined,
  reverse: boolean,
  dst: Float64Array,
): Float64Array {
  const len = src.length;
  const step = reverse ? -1 : 1;
  const first = reverse ? len - 1 : 0;
  let read = src;
  let write = dst;
  for (let s = 0; s < sos.length; s++) {
    const [b0, b1, b2, a0, a1, a2] = sos[s];
    const [nb0, nb1, nb2, na1, na2] = [b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0];
    let z0 = zi ? zi[s][0] : 0;
    let z1 = zi ? zi[s][1] : 0;
    for (let k = 0, i = first; k < len; k++, i += step) {
      const xi = read[i];
      const yi = nb0 * xi + z0;
      z0 = nb1 * xi - na1 * yi + z1;
      z1 = nb2 * xi - na2 * yi;
      write[i] = yi;
    }
    const swap = read;
    read = write;
    write = swap;
  }
  return read;
}

/** Allocating wrapper, for callers outside the hot chain. */
export function sosfilt(sos: Biquad[], x: Float64Array, zi?: number[][], reverse = false): Float64Array {
  const other = new Float64Array(x.length);
  const src = Float64Array.from(x);
  const held = sosfiltPingPong(sos, src, zi, reverse, other);
  return held === src ? Float64Array.from(held) : held;
}

/** scipy.signal.sosfilt_zi: per-section steady state, each scaled by the DC
 * gain of every section before it. */
export function sosfiltZi(sos: Biquad[]): number[][] {
  let scale = 1;
  return sos.map((s) => {
    const b = [s[0], s[1], s[2]];
    const a = [s[3], s[4], s[5]];
    const zi = scaled(lfilterZi(b, a), scale);
    scale *= (b[0] + b[1] + b[2]) / (a[0] + a[1] + a[2]);
    return zi;
  });
}

/** scipy.signal.sosfiltfilt, including its padlen rule (sections with a zero
 * b2/a2 need less padding). */
export function sosfiltfiltInto(sos: Biquad[], x: Float64Array, sc: Scratch): void {
  const zeroB2 = sos.filter((s) => s[2] === 0).length;
  const zeroA2 = sos.filter((s) => s[5] === 0).length;
  const edge = 3 * (2 * sos.length + 1 - Math.min(zeroB2, zeroA2));
  if (x.length <= edge) throw new Error(`sosfiltfilt: signal of ${x.length} is too short for padlen ${edge}`);
  const ext = x.length + 2 * edge;
  const zi = sosfiltZi(sos);
  const p = sc.p.subarray(0, ext);
  const q = sc.q.subarray(0, ext);
  oddExtInto(x, edge, p);
  const fwd = sosfiltPingPong(sos, p, zi.map((z) => scaled(z, p[0])), false, q);
  const other = fwd === p ? q : p;
  const back = sosfiltPingPong(sos, fwd, zi.map((z) => scaled(z, fwd[ext - 1])), true, other);
  x.set(back.subarray(edge, edge + x.length));
}

/** Allocating wrapper, for callers outside the hot chain. */
export function sosfiltfilt(sos: Biquad[], x: Float64Array): Float64Array {
  const y = Float64Array.from(x);
  sosfiltfiltInto(sos, y, makeScratch(x.length));
  return y;
}

/** scipy.signal.iirnotch(w0, Q) with fs=2, i.e. w0 normalised to Nyquist.
 * Orfanidis formula 11.3.4/11.3.6; at the -3 dB point beta reduces to
 * tan(bw/2). */
export function iirnotch(w0: number, Q: number): { b: number[]; a: number[] } {
  const bw = (w0 / Q) * Math.PI;
  const w = w0 * Math.PI;
  const beta = Math.tan(bw / 2);
  const gain = 1 / (1 + beta);
  return {
    b: [gain, -2 * gain * Math.cos(w), gain],
    a: [1, -2 * gain * Math.cos(w), 2 * gain - 1],
  };
}

/** scipy.signal.butter(order, wn, "lowpass", output="sos") for even orders:
 * Butterworth analog prototype -> lp2lp -> bilinear -> conjugate pairs as
 * second-order sections.
 *
 * Even orders only -- the review chain uses 4, and an odd order's lone real
 * pole would need the half-section case for no caller. */
export function butterLowpassSos(order: number, wn: number): Biquad[] {
  if (order % 2 !== 0) throw new Error(`butterLowpassSos: even orders only, got ${order}`);

  // Analog prototype poles, p = -exp(i*pi*k/(2N)) for k in arange(-N+1, N, 2),
  // then lp2lp scales them by the pre-warped cutoff.
  const warped = 4 * Math.tan((Math.PI * wn) / 2); // fs = 2 in scipy's digital path
  const re: number[] = [];
  const im: number[] = [];
  for (let k = -order + 1; k < order; k += 2) {
    const theta = (Math.PI * k) / (2 * order);
    re.push(-Math.cos(theta) * warped);
    im.push(-Math.sin(theta) * warped);
  }

  // Bilinear transform, fs = 2 so fs2 = 4. All zeros land at z = -1.
  // p_z = (fs2 + p) / (fs2 - p), with p = re + i*im.
  const fs2 = 4;
  const pzRe: number[] = [];
  const pzIm: number[] = [];
  for (let i = 0; i < order; i++) {
    const nRe = fs2 + re[i];
    const dRe = fs2 - re[i];
    const den = dRe * dRe + im[i] * im[i]; // |fs2 - p|^2
    pzRe.push((nRe * dRe - im[i] * im[i]) / den);
    pzIm.push((im[i] * (nRe + dRe)) / den);
  }

  // k_z = k * prod(fs2 - z) / prod(fs2 - p). There are no analog zeros, and the
  // poles are conjugate pairs, so each pair contributes a real factor and the
  // whole product stays real -- no complex accumulator needed.
  let denom = 1;
  for (let i = 0; i < order / 2; i++) {
    denom *= fs2 * fs2 - 2 * fs2 * re[i] + (re[i] * re[i] + im[i] * im[i]);
  }
  const k = Math.pow(warped, order) / denom; // lp2lp: k *= wo^degree, degree = order

  // Poles come out in conjugate order, so i pairs with order-1-i. Sections are
  // ordered farthest-from-the-unit-circle first, matching zpk2sos, which also
  // folds the whole gain into the first section.
  const sections: { r: number; sos: Biquad }[] = [];
  for (let i = 0; i < order / 2; i++) {
    const j = order - 1 - i;
    const sumRe = pzRe[i] + pzRe[j];
    const prod = pzRe[i] * pzRe[j] - pzIm[i] * pzIm[j];
    sections.push({ r: Math.hypot(pzRe[i], pzIm[i]), sos: [1, 2, 1, 1, -sumRe, prod] });
  }
  sections.sort((x, y) => x.r - y.r);
  sections[0].sos = [k, 2 * k, k, sections[0].sos[3], sections[0].sos[4], sections[0].sos[5]];
  return sections.map((s) => s.sos);
}

/** Harmonics of the mains frequency a notch can actually remove -- the port of
 * filters.mains_harmonics, including its _NYQ_MARGIN. */
export function mainsHarmonics(mainsFreq: number, fs: number, upTo?: number): number[] {
  if (mainsFreq <= 0) return [];
  let limit = (fs / 2) * 0.99;
  if (upTo != null) limit = Math.min(limit, upTo);
  const out: number[] = [];
  // start + i*step, like np.arange -- not a running sum, which drifts.
  for (let i = 1; i * mainsFreq < limit; i++) out.push(i * mainsFreq);
  return out;
}
