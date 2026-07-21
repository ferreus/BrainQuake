import { useEffect, useMemo, useRef } from "react";
import { Modal, Stack, Text } from "@mantine/core";
import { useEdfWindow } from "../../api/queries/useEdf";
import { hotColor } from "../../lib/colormap";
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

/** Per-channel drill-down: raw target-window trace + STFT spectrogram,
 * mirroring client_ictal.py's ei_press_func popup (left-click a bar in the
 * EI chart). */
export function SpectrogramModal({ subjectId, edfArtifactId, channel, range, bandLow, bandHigh, onClose }: SpectrogramModalProps) {
  const enabled = channel != null && range != null;
  const { data } = useEdfWindow(
    subjectId,
    edfArtifactId,
    {
      start: range?.[0] ?? 0,
      end: range?.[1] ?? 0,
      channels: channel ? [channel] : undefined,
      bandLow,
      bandHigh,
    },
    enabled,
  );

  const traceRef = useRef<HTMLCanvasElement>(null);
  const specRef = useRef<HTMLCanvasElement>(null);
  const samples = data?.data?.[0];
  const fs = data?.fs;

  const spectrogram = useMemo(() => {
    if (!samples || !fs) return null;
    return computeSpectrogram(samples, fs, { maxFreqHz: 300 });
  }, [samples, fs]);

  useEffect(() => {
    const canvas = traceRef.current;
    if (!canvas || !samples) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    const min = Math.min(...samples);
    const max = Math.max(...samples);
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
  }, [samples]);

  useEffect(() => {
    const canvas = specRef.current;
    if (!canvas || !spectrogram) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { times, freqs, values } = spectrogram;
    if (times.length === 0 || freqs.length === 0) return;
    const cellW = canvas.width / times.length;
    const cellH = canvas.height / freqs.length;
    for (let f = 0; f < freqs.length; f++) {
      const y = canvas.height - (f + 1) * cellH;
      for (let t = 0; t < times.length; t++) {
        const v = values[f][t];
        const norm = (v - VMIN) / (VMAX - VMIN);
        ctx.fillStyle = hotColor(norm);
        ctx.fillRect(t * cellW, y, cellW + 1, cellH + 1);
      }
    }
  }, [spectrogram]);

  return (
    <Modal opened={channel != null} onClose={onClose} title={channel ? `Channel ${channel}` : ""} size="lg">
      <Stack>
        <div>
          <Text size="xs" c="dimmed">
            Raw signal (target window)
          </Text>
          <canvas ref={traceRef} width={560} height={100} style={{ width: "100%", height: 100 }} />
        </div>
        <div>
          <Text size="xs" c="dimmed">
            Spectrogram (0-300Hz)
          </Text>
          <canvas ref={specRef} width={560} height={160} style={{ width: "100%", height: 160 }} />
        </div>
      </Stack>
    </Modal>
  );
}
