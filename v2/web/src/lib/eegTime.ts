/** Wall-clock helpers for EEG time axes.
 *
 * Ports v2/tools/show_edf.py's fmt_clock/parse_clock: the server exposes the
 * recording's start as an ISO timestamp and every other time in the API stays
 * seconds-from-start, so the modular arithmetic for a recording that crosses
 * midnight lives here rather than in an endpoint. */

/** Seconds since midnight of an ISO timestamp, or null when there is none. */
export function midnightSeconds(measDate: string | null | undefined): number | null {
  if (!measDate) return null;
  const d = new Date(measDate);
  if (Number.isNaN(d.getTime())) return null;
  return d.getUTCHours() * 3600 + d.getUTCMinutes() * 60 + d.getUTCSeconds();
}

const pad = (n: number) => String(Math.floor(n)).padStart(2, "0");

/** HH:MM:SS, wrapping past midnight. */
export function formatClock(seconds: number): string {
  const s = ((seconds % 86400) + 86400) % 86400;
  return `${pad(s / 3600)}:${pad((s / 60) % 60)}:${pad(s % 60)}`;
}

/** H:MM:SS from the recording start. Not formatClock: elapsed must not wrap at
 * 24 h, and a leading zero hour reads as a clock time. */
export function formatElapsed(seconds: number): string {
  const s = Math.max(0, seconds);
  return `${Math.floor(s / 3600)}:${pad((s / 60) % 60)}:${pad(s % 60)}`;
}

/** Axis label for `elapsed` seconds into the recording: wall clock when the
 * header carried a start time, elapsed seconds otherwise. */
export function formatAxisTime(elapsed: number, originSeconds: number | null): string {
  return originSeconds == null ? `${elapsed.toFixed(0)}s` : formatClock(originSeconds + elapsed);
}

/** Parses a jump-to-time entry into elapsed seconds from the recording start.
 * `HH:MM:SS` / `MM:SS` is a wall clock resolved against `originSeconds` (a
 * clock earlier than the start means the recording crossed midnight); a bare
 * number is already elapsed seconds. Null when it parses as neither. */
export function parseTimeInput(text: string, originSeconds: number | null): number | null {
  const t = text.trim();
  if (!t) return null;
  if (!t.includes(":")) {
    const n = Number(t);
    return Number.isFinite(n) && n >= 0 ? n : null;
  }
  const parts = t.split(":").map((p) => Number(p));
  if (parts.length > 3 || parts.some((n) => !Number.isFinite(n) || n < 0)) return null;
  const [h, m, s] = parts.length === 3 ? parts : [0, parts[0], parts[1] ?? 0];
  const clock = h * 3600 + m * 60 + s;
  if (originSeconds == null) return clock; // no start time: read it as elapsed
  return ((clock - originSeconds) % 86400 + 86400) % 86400;
}
