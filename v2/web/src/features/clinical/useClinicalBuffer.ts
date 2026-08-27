import { useEffect, useMemo, useState } from "react";
import { useEdfWindow } from "../../api/queries/useEdf";
import { filterForReview } from "./reviewFilter";
import type { ClinicalMontage } from "./useClinicalViewState";

/** Filter context fetched either side of the usable span, so the client filters
 * the same padded input the server would. Covers app/services/edf.py's own worst
 * case: _TC_SETTLE_TAUS (5) * the longest TC preset (2 s). This is pad, not
 * buffer -- enlarging it buys no extra panning, only samples nobody draws. */
const PAD_SECONDS = 10;
/** The whole per-request budget, and it must not exceed MAX_WINDOW_SECONDS in
 * app/services/edf.py -- the server 400s above it. Everything below is derived
 * from this so a request can never be built that the server would reject. */
const MAX_REQUEST_SECONDS = 120;

export interface ClinicalWindow {
  fs: number;
  start: number;
  end: number;
  channels: string[];
  /** data[rowIndex] holds that row's samples for the visible window. */
  data: Float64Array[];
}

interface BufferSpan {
  start: number;
  end: number;
}

interface ClinicalBufferParams {
  timeStart: number;
  pageSeconds: number;
  durationSec: number;
  channels: string[];
  tc: number | null;
  highCut: number | null;
  mainsFreq: number;
  montage: ClinicalMontage;
}

/** Splits the request budget into filter pad and displayable core. The pad is
 * given up first: on a page so long that the budget cannot hold both, showing
 * the page at all beats filtering it with more context. */
function spanFor(pageSeconds: number): { pad: number; core: number } {
  const pad = Math.min(PAD_SECONDS, Math.max(0, (MAX_REQUEST_SECONDS - pageSeconds) / 2));
  return { pad, core: Math.max(pageSeconds, MAX_REQUEST_SECONDS - 2 * pad) };
}

/** Does this buffer's usable core still contain the visible window? The pad is
 * excluded: displaying it would show samples filtered without enough context. */
function covers(
  buf: BufferSpan,
  timeStart: number,
  pageSeconds: number,
  durationSec: number,
  pad: number,
): boolean {
  const coreStart = buf.start <= 0 ? 0 : buf.start + pad;
  const coreEnd = buf.end >= durationSec ? durationSec : buf.end - pad;
  const eps = 1e-6;
  return timeStart >= coreStart - eps && timeStart + pageSeconds <= coreEnd + eps;
}

/**
 * Fetches raw EEG a few pages at a time and filters it on the client, so
 * panning inside the buffer costs no network at all and changing TC, high cut,
 * mains or Referential/CAR costs no network either -- none of those reach the
 * request. Only Bipolar does, because the pairing is server-side.
 *
 * The alternative, one filtered request per pan tick, is what this replaces:
 * PAN_TIME steps a fifth of a page, so every tick was a fresh cache key and a
 * ~95 ms round trip.
 */
export function useClinicalBuffer(
  subjectId: number,
  edfArtifactId: number,
  params: ClinicalBufferParams,
  enabled: boolean,
) {
  const { timeStart, pageSeconds, durationSec, channels, tc, highCut, mainsFreq, montage } = params;
  const [buffer, setBuffer] = useState<BufferSpan | null>(null);
  const { pad, core } = spanFor(pageSeconds);

  useEffect(() => {
    setBuffer((prev) => {
      if (prev && covers(prev, timeStart, pageSeconds, durationSec, pad)) return prev;
      const maxCoreStart = Math.max(0, durationSec - core);
      const coreStart = Math.min(maxCoreStart, Math.max(0, timeStart - (core - pageSeconds) / 2));
      const start = Math.max(0, coreStart - pad);
      // The derived span lands exactly on the budget, so clamp rather than trust
      // it to: a float hair over is a 400 from the server, not a shorter window.
      return { start, end: Math.min(durationSec, start + MAX_REQUEST_SECONDS, coreStart + core + pad) };
    });
  }, [timeStart, pageSeconds, core, pad, durationSec]);

  // No tc / bandHigh / mainsFreq: leaving them off the request is what keeps
  // them out of the cache key, and a request with no filter params returns the
  // exact raw slice with no server-side pad or trim.
  const bufferParams = useMemo(
    () => ({
      start: buffer?.start ?? 0,
      end: buffer?.end ?? 0,
      channels,
      reference: montage === "bipolar" ? ("bipolar" as const) : ("none" as const),
    }),
    [buffer, channels, montage],
  );

  const query = useEdfWindow(
    subjectId,
    edfArtifactId,
    bufferParams,
    enabled && buffer != null && channels.length > 0 && durationSec > 0,
    undefined,
    false,
  );

  const raw = query.data;
  const filtered = useMemo(() => {
    if (!raw || raw.data.length === 0) return undefined;
    const rows = raw.data.map((d) => Float64Array.from(d));
    try {
      return filterForReview(rows, raw.fs, {
        tc,
        hicut: highCut,
        mainsFreq,
        // Bipolar arrives already referenced; CAR must not go on top of it.
        reference: montage === "car" ? "car" : "none",
      });
    } catch (err) {
      // Too few samples for filtfilt's padding, on a recording shorter than the
      // filter needs. Showing it unfiltered beats blanking the tab.
      console.warn("clinical review filter skipped", err);
      return rows;
    }
  }, [raw, tc, highCut, mainsFreq, montage]);

  const data = useMemo<ClinicalWindow | undefined>(() => {
    if (!raw || !filtered || filtered.length === 0) return undefined;
    const offset = Math.round((timeStart - raw.start) * raw.fs);
    const length = filtered[0].length;
    // The buffer in hand can be the previous one (keepPreviousData) or one
    // still being replaced; slice it whenever the window genuinely falls inside.
    if (offset < 0 || offset >= length) return undefined;
    const end = Math.min(length, offset + Math.round(pageSeconds * raw.fs));
    return {
      fs: raw.fs,
      start: timeStart,
      end: timeStart + pageSeconds,
      channels: raw.channels,
      data: filtered.map((r) => r.subarray(offset, end)),
    };
  }, [raw, filtered, timeStart, pageSeconds]);

  return { data, isError: query.isError, error: query.error };
}
