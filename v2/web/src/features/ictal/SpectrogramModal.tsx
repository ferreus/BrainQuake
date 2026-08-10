import { useEffect, useMemo, useState } from "react";
import { Loader, Modal, Stack, Text } from "@mantine/core";
import { ApiError } from "../../api/client";
import { useEdfWindow } from "../../api/queries/useEdf";
import { hotColorRgb } from "../../lib/colormap";
import { computeSpectrogram } from "../../lib/spectrogram";

interface SpectrogramModalProps {
  subjectId: number;
  edfArtifactId: number;
  channel: string | null;
  range: [number, number] | null;
  bandLow: number;
  bandHigh: number;
  onClose: () => void;
}

// Matches the legacy z-scored spectrogram's fixed color range (plt.cm.hot,
// vmin=-0.8, vmax=2) so the visual thresholds line up with the Qt client.
const VMIN = -0.8;
const VMAX = 2;

// The window endpoint refuses anything longer (services/edf.py's
// MAX_WINDOW_SECONDS), and seizure target windows regularly exceed it.
const MAX_WINDOW_SECONDS = 60;

/** Per-channel drill-down: raw target-window trace + STFT spectrogram,
 * mirroring client_ictal.py's ei_press_func popup (left-click a bar in the
 * EI chart). */
export function SpectrogramModal({ subjectId, edfArtifactId, channel, range, bandLow, bandHigh, onClose }: SpectrogramModalProps) {
  const enabled = channel != null && range != null;
  const start = range?.[0] ?? 0;
  const end = Math.min(range?.[1] ?? 0, start + MAX_WINDOW_SECONDS);
  const truncated = (range?.[1] ?? 0) > end;

  const { data, isFetching, error } = useEdfWindow(
    subjectId,
    edfArtifactId,
    { start, end, channels: channel ? [channel] : undefined, bandLow, bandHigh },
    enabled,
    0,
  );

  // Element in state, not a ref: the canvases mount a commit later than the
  // modal opens (Mantine's Transition), so a ref-only effect would draw into
  // nothing and never re-run once the element finally attached.
  const [traceCanvas, setTraceCanvas] = useState<HTMLCanvasElement | null>(null);
  const [specCanvas, setSpecCanvas] = useState<HTMLCanvasElement | null>(null);

  const samples = data?.data?.[0];
  const fs = data?.fs;

  const spectrogram = useMemo(() => {
    if (!samples?.length || !fs) return null;
    return computeSpectrogram(samples, fs, { maxFreqHz: 300 });
  }, [samples, fs]);

  useEffect(() => {
    const ctx = traceCanvas?.getContext("2d");
    if (!ctx || !traceCanvas || !samples?.length) return;
    const { width, height } = traceCanvas;
    ctx.clearRect(0, 0, width, height);
    let min = Infinity;
    let max = -Infinity;
    for (const v of samples) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
    const span = max - min || 1;
    ctx.strokeStyle = "#2a78d6";
    ctx.lineWidth = 1;
    ctx.beginPath();
    samples.forEach((v, i) => {
      const x = (i / Math.max(1, samples.length - 1)) * width;
      const y = height - ((v - min) / span) * height;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }, [samples, traceCanvas]);

  useEffect(() => {
    const ctx = specCanvas?.getContext("2d");
    if (!ctx || !specCanvas || !spectrogram) return;
    const { times, freqs, values } = spectrogram;
    const { width, height } = specCanvas;
    ctx.clearRect(0, 0, width, height);
    if (times.length === 0 || freqs.length === 0) return;

    // Per-pixel, not per cell: a 60s window is ~1200 time bins x 513 frequency
    // rows, and one fillRect per cell is 600k calls into a 560x160 canvas.
    const image = ctx.createImageData(width, height);
    for (let py = 0; py < height; py++) {
      const f = Math.min(freqs.length - 1, Math.floor(((height - 1 - py) / height) * freqs.length));
      const row = values[f];
      for (let px = 0; px < width; px++) {
        const t = Math.min(times.length - 1, Math.floor((px / width) * times.length));
        const [r, g, b] = hotColorRgb((row[t] - VMIN) / (VMAX - VMIN));
        const i = (py * width + px) * 4;
        image.data[i] = r;
        image.data[i + 1] = g;
        image.data[i + 2] = b;
        image.data[i + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
  }, [spectrogram, specCanvas]);

  const status = (() => {
    if (error) return error instanceof ApiError ? error.message : String(error);
    if (isFetching && !samples) return null; // spinner below
    if (samples && samples.length === 0) return "The server returned no samples for this window.";
    if (samples && spectrogram && spectrogram.times.length === 0) {
      return "Window is too short for a spectrogram (needs at least half a second).";
    }
    return null;
  })();

  return (
    <Modal opened={channel != null} onClose={onClose} title={channel ? `Channel ${channel}` : ""} size="lg">
      <Stack>
        <Text size="xs" c="dimmed">
          {truncated
            ? `Target window ${start.toFixed(1)}-${(range?.[1] ?? 0).toFixed(1)}s, showing the first ${MAX_WINDOW_SECONDS}s`
            : `Target window ${start.toFixed(1)}-${end.toFixed(1)}s`}
          {` · filtered ${bandLow}-${bandHigh}Hz, single channel so no common-average reference`}
        </Text>
        {isFetching && !samples && <Loader size="sm" />}
        {status && (
          <Text size="xs" c="red">
            {status}
          </Text>
        )}
        {samples && samples.length > 0 && (
          <>
            <div>
              <Text size="xs" c="dimmed">
                Raw signal
              </Text>
              <canvas ref={setTraceCanvas} width={560} height={100} style={{ width: "100%", height: 100 }} />
            </div>
            <div>
              <Text size="xs" c="dimmed">
                Spectrogram (0-300Hz)
              </Text>
              <canvas ref={setSpecCanvas} width={560} height={160} style={{ width: "100%", height: 160 }} />
            </div>
          </>
        )}
      </Stack>
    </Modal>
  );
}
