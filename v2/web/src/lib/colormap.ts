// Reimplements matplotlib's "hot" colormap (the control points below are
// its exact `_cm.py` breakpoints) so the web spectrogram matches the Qt
// client's `plt.cm.hot` rendering pixel-for-pixel in hue.
function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function redChannel(t: number): number {
  if (t <= 0.365079) return lerp(0.0416, 1, t / 0.365079);
  return 1;
}

function greenChannel(t: number): number {
  if (t <= 0.365079) return 0;
  if (t <= 0.746032) return lerp(0, 1, (t - 0.365079) / (0.746032 - 0.365079));
  return 1;
}

function blueChannel(t: number): number {
  if (t <= 0.746032) return 0;
  return lerp(0, 1, (t - 0.746032) / (1 - 0.746032));
}

/** Maps a normalized value in [0, 1] to matplotlib-"hot" as [r, g, b] bytes.
 * Values outside [0, 1] are clamped, matching pcolormesh's vmin/vmax clipping. */
export function hotColorRgb(t: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, t));
  return [
    Math.round(redChannel(clamped) * 255),
    Math.round(greenChannel(clamped) * 255),
    Math.round(blueChannel(clamped) * 255),
  ];
}
