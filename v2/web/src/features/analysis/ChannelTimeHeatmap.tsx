import { useEffect, useMemo, useRef } from "react";
import { interpolatePlasma } from "d3-scale-chromatic";

interface ChannelTimeHeatmapProps {
  /** (channels x windows), values normalised to [0, 1]. */
  matrix: number[][];
  channels: string[];
  /** Seconds per column, relative to the onset (so t=0 is the mark). */
  startTimes: number[];
  height?: number;
}

const LABEL_WIDTH = 64;
const AXIS_HEIGHT = 22;

/** 256-entry lookup, so the per-pixel loop never parses a CSS colour string. */
const LUT = (() => {
  const lut = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const m = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(interpolatePlasma(i / 255));
    if (m) {
      lut[i * 3] = Number(m[1]);
      lut[i * 3 + 1] = Number(m[2]);
      lut[i * 3 + 2] = Number(m[3]);
    }
  }
  return lut;
})();

/**
 * Fragility over time: one row per contact, one column per analysis window.
 * Built per-pixel into an ImageData and blitted once -- a fillRect per cell is
 * ~44k calls on a 184-contact seizure (the same reason SpectrogramModal does it
 * this way).
 */
export function ChannelTimeHeatmap({
  matrix, channels, startTimes, height = 420,
}: ChannelTimeHeatmapProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nWindows = matrix[0]?.length ?? 0;

  // The heatmap itself, at native matrix resolution; scaled up on draw.
  const bitmap = useMemo(() => {
    if (matrix.length === 0 || nWindows === 0) return null;
    const img = new ImageData(nWindows, matrix.length);
    for (let r = 0; r < matrix.length; r++) {
      const row = matrix[r];
      for (let c = 0; c < nWindows; c++) {
        const v = row[c];
        const idx = Number.isFinite(v) ? Math.max(0, Math.min(255, Math.round(v * 255))) : 0;
        const o = (r * nWindows + c) * 4;
        img.data[o] = LUT[idx * 3];
        img.data[o + 1] = LUT[idx * 3 + 1];
        img.data[o + 2] = LUT[idx * 3 + 2];
        img.data[o + 3] = 255;
      }
    }
    return img;
  }, [matrix, nWindows]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !bitmap) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const plotWidth = width - LABEL_WIDTH;
    const plotHeight = canvas.height - AXIS_HEIGHT;
    ctx.clearRect(0, 0, width, canvas.height);

    const off = document.createElement("canvas");
    off.width = bitmap.width;
    off.height = bitmap.height;
    off.getContext("2d")?.putImageData(bitmap, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(off, LABEL_WIDTH, 0, plotWidth, plotHeight);

    const rowHeight = plotHeight / channels.length;
    ctx.fillStyle = "#888";
    ctx.font = "9px sans-serif";
    ctx.textBaseline = "middle";
    // Label every nth contact -- 184 of them will not fit legibly.
    const step = Math.max(1, Math.ceil(12 / rowHeight));
    for (let i = 0; i < channels.length; i += step) {
      ctx.fillText(channels[i], 4, (i + 0.5) * rowHeight);
    }

    // Time axis, with the onset marked: it is what every score is relative to.
    ctx.textBaseline = "top";
    ctx.textAlign = "center";
    const t0 = startTimes[0] ?? 0;
    const t1 = startTimes[startTimes.length - 1] ?? 1;
    const xFor = (t: number) => LABEL_WIDTH + ((t - t0) / (t1 - t0 || 1)) * plotWidth;
    const tickStep = Math.max(1, Math.round((t1 - t0) / 8));
    for (let t = Math.ceil(t0); t <= t1; t += tickStep) {
      ctx.fillStyle = "#888";
      ctx.fillText(`${t}s`, xFor(t), plotHeight + 4);
    }
    if (t0 < 0 && t1 > 0) {
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(xFor(0), 0);
      ctx.lineTo(xFor(0), plotHeight);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#fff";
      ctx.fillText("onset", xFor(0), 2);
    }
  }, [bitmap, channels, startTimes]);

  if (!bitmap) return null;

  return (
    <canvas
      ref={canvasRef}
      width={900}
      height={height}
      style={{ width: "100%", height, display: "block" }}
      role="img"
      aria-label="Fragility heatmap: contacts by time"
    />
  );
}
