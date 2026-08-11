import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch } from "react";
import { Group, Loader, Stack, Text } from "@mantine/core";
import { ApiError } from "../../api/client";
import { useEdfMeta, useEdfWindow } from "../../api/queries/useEdf";
import { EdfLoadErrorPanel } from "./EdfLoadErrorPanel";
import type { EegViewerAction, EegViewerState } from "./useEegViewerState";

export interface EegMarker {
  time: number;
  color: string;
  label?: string;
}

interface EegCanvasProps {
  subjectId: number;
  edfArtifactId: number;
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
  markers?: EegMarker[];
  /** Per-channel detected-event time ranges (seconds), keyed by channel name.
   * Drawn as horizontal segments on each channel's row -- the interictal HFO
   * detection overlay (client_inter.py's _draw_hfo_overlay). */
  eventOverlays?: Record<string, [number, number][]>;
  onCanvasClick?: (time: number) => void;
}

const MIN_CANVAS_HEIGHT = 320;
const FALLBACK_CANVAS_SIZE = { width: 900, height: 480 };

/**
 * Stacked multi-channel trace viewer -- Canvas2D, not WebGL (redraws are
 * interaction-triggered, not continuous-scroll; ~20 visible rows x a few
 * thousand samples is trivial for moveTo/lineTo). Reproduces the legacy
 * client_ictal.py/client_inter.py LineCollection viewer: each channel is one
 * polyline at a fixed row offset (row pitch dr = 0.7 * global amplitude
 * range), with vertical marker lines (baseline/target, or HFO events in
 * Phase 4) drawn on the same canvas in a fixed order.
 */
export function EegCanvas({ subjectId, edfArtifactId, state, dispatch, markers = [], eventOverlays, onCanvasClick }: EegCanvasProps) {
  const { data: meta, isError: metaIsError, error: metaError, refetch: refetchMeta } = useEdfMeta(subjectId, edfArtifactId);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Sized off the container rather than a fixed constant, so the canvas fills
  // whatever vertical space the surrounding layout actually gives it instead
  // of leaving a blank gap below a fixed-height buffer.
  const [canvasSize, setCanvasSize] = useState(FALLBACK_CANVAS_SIZE);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let timer: number | undefined;
    // Trailing debounce + same-size bail-out: panel toggles and window drags
    // fire bursts of resize events, and a full multi-channel redraw per event
    // blocks the main thread for seconds.
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        const width = Math.max(1, Math.round(rect.width));
        const height = Math.max(MIN_CANVAS_HEIGHT, Math.round(rect.height));
        setCanvasSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
      }, 100);
    });
    observer.observe(el);
    return () => {
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, []);

  const allChannels = useMemo(() => meta?.channels ?? [], [meta]);
  const remainingChannels = useMemo(
    () => allChannels.filter((c) => !state.excludedChannels.has(c)),
    [allChannels, state.excludedChannels],
  );
  const visibleChannels = useMemo(
    () => remainingChannels.slice(state.dispChansStart, state.dispChansStart + state.dispChansNum),
    [remainingChannels, state.dispChansStart, state.dispChansNum],
  );

  const {
    data: windowData,
    isError: windowIsError,
    error: windowError,
  } = useEdfWindow(
    subjectId,
    edfArtifactId,
    {
      start: state.dispTimeStart,
      end: state.dispTimeStart + state.dispTimeWin,
      channels: visibleChannels,
      bandLow: state.filterEnabled ? state.filterBandLow : undefined,
      bandHigh: state.filterEnabled ? state.filterBandHigh : undefined,
      mainsFreq: state.mainsFreq,
    },
    visibleChannels.length > 0,
  );

  // Row pitch from the traces actually on screen, not the recording's global
  // range: a 60-140Hz display filter leaves ~1% of the unfiltered amplitude, so
  // a pitch fixed to the unfiltered range drew every filtered trace flat.
  // Median RMS over the visible channels rather than a peak, so one saturated
  // contact can't shrink the other nineteen.
  const dr = useMemo(() => {
    const rows = windowData?.data ?? [];
    const rms = rows
      .filter((s) => s.length > 0)
      .map((s) => {
        let sum = 0;
        for (const v of s) sum += v * v;
        return Math.sqrt(sum / s.length);
      })
      .sort((a, b) => a - b);
    const median = rms.length ? rms[Math.floor(rms.length / 2)] : 0;
    if (median > 0) return 6 * median; // ~+/-3 sigma per row
    return meta ? 0.7 * (meta.amplitude_range.max - meta.amplitude_range.min) || 1 : 1;
  }, [windowData, meta]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !windowData) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const nRows = state.dispChansNum;
    const rowHeight = height / nRows;
    const scale = rowHeight / 2 / (dr / 2);

    ctx.strokeStyle = "rgba(128,128,128,0.2)";
    ctx.fillStyle = "#888";
    ctx.font = "10px monospace";
    for (let i = 0; i < nRows; i++) {
      const y = i * rowHeight + rowHeight / 2;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      const name = windowData.channels[i];
      if (name) ctx.fillText(name, 4, y - 2);
    }

    ctx.strokeStyle = "#111";
    ctx.lineWidth = 1;
    windowData.channels.forEach((_, rowIndex) => {
      const samples = windowData.data[rowIndex];
      if (!samples || samples.length === 0) return;
      const rowCenter = rowIndex * rowHeight + rowHeight / 2;
      ctx.beginPath();
      samples.forEach((v, i) => {
        const x = (i / Math.max(1, samples.length - 1)) * width;
        const y = rowCenter - v * state.dispWaveMul * scale;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    });

    const winStart = state.dispTimeStart;
    const winEnd = state.dispTimeStart + state.dispTimeWin;

    if (eventOverlays) {
      ctx.strokeStyle = "#d03b3b";
      ctx.lineWidth = 2.5;
      windowData.channels.forEach((name, rowIndex) => {
        const events = eventOverlays[name];
        if (!events) return;
        const rowCenter = rowIndex * rowHeight + rowHeight / 2;
        events.forEach(([s, e]) => {
          if (e < winStart || s > winEnd) return;
          const x0 = ((Math.max(s, winStart) - winStart) / (winEnd - winStart)) * width;
          const x1 = ((Math.min(e, winEnd) - winStart) / (winEnd - winStart)) * width;
          ctx.beginPath();
          ctx.moveTo(x0, rowCenter);
          ctx.lineTo(x1, rowCenter);
          ctx.stroke();
        });
      });
    }

    markers.forEach((m) => {
      if (m.time < winStart || m.time > winEnd) return;
      const x = ((m.time - winStart) / (winEnd - winStart)) * width;
      ctx.strokeStyle = m.color;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    });
  }, [windowData, state.dispChansNum, state.dispWaveMul, state.dispTimeStart, state.dispTimeWin, dr, markers, eventOverlays, canvasSize]);

  function handleWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    e.preventDefault();
    dispatch({ type: "PAN_TIME", direction: e.deltaY > 0 ? 1 : -1 });
  }

  function handleClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (!onCanvasClick) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const fraction = (e.clientX - rect.left) / rect.width;
    onCanvasClick(state.dispTimeStart + fraction * state.dispTimeWin);
  }

  if (metaIsError) {
    return <EdfLoadErrorPanel title="Failed to load EDF recording" error={metaError} onRetry={() => refetchMeta()} />;
  }

  if (!meta) {
    return (
      <Group justify="center" p="xl">
        <Loader size="sm" />
      </Group>
    );
  }

  return (
    <Stack gap={4} style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
      <div ref={containerRef} style={{ flex: 1, minHeight: MIN_CANVAS_HEIGHT }}>
        <canvas
          ref={canvasRef}
          width={canvasSize.width}
          height={canvasSize.height}
          style={{
            display: "block",
            width: "100%",
            height: "100%",
            background: "#fafafa",
            cursor: onCanvasClick ? "crosshair" : "default",
          }}
          onWheel={handleWheel}
          onClick={handleClick}
        />
      </div>
      {windowIsError ? (
        <Text size="xs" c="red">
          Failed to load this window: {windowError instanceof ApiError ? windowError.message : String(windowError)}
        </Text>
      ) : (
        <Text size="xs" c="dimmed">
          {state.dispTimeStart.toFixed(1)}s &ndash; {(state.dispTimeStart + state.dispTimeWin).toFixed(1)}s (scroll to pan)
        </Text>
      )}
    </Stack>
  );
}
