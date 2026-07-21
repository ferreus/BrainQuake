import { useEffect, useMemo, useRef } from "react";
import type { Dispatch } from "react";
import { Group, Loader, Stack, Text } from "@mantine/core";
import { useEdfMeta, useEdfWindow } from "../../api/queries/useEdf";
import { EegChannelList } from "./EegChannelList";
import { EegToolbar } from "./EegToolbar";
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
  onCanvasClick?: (time: number) => void;
}

const CANVAS_HEIGHT = 480;
const CANVAS_WIDTH = 900;

/**
 * Stacked multi-channel trace viewer -- Canvas2D, not WebGL (redraws are
 * interaction-triggered, not continuous-scroll; ~20 visible rows x a few
 * thousand samples is trivial for moveTo/lineTo). Reproduces the legacy
 * client_ictal.py/client_inter.py LineCollection viewer: each channel is one
 * polyline at a fixed row offset (row pitch dr = 0.7 * global amplitude
 * range), with vertical marker lines (baseline/target, or HFO events in
 * Phase 4) drawn on the same canvas in a fixed order.
 */
export function EegCanvas({ subjectId, edfArtifactId, state, dispatch, markers = [], onCanvasClick }: EegCanvasProps) {
  const { data: meta } = useEdfMeta(subjectId, edfArtifactId);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const allChannels = useMemo(() => meta?.channels ?? [], [meta]);
  const remainingChannels = useMemo(
    () => allChannels.filter((c) => !state.excludedChannels.has(c)),
    [allChannels, state.excludedChannels],
  );
  const visibleChannels = useMemo(
    () => remainingChannels.slice(state.dispChansStart, state.dispChansStart + state.dispChansNum),
    [remainingChannels, state.dispChansStart, state.dispChansNum],
  );

  const { data: windowData } = useEdfWindow(
    subjectId,
    edfArtifactId,
    {
      start: state.dispTimeStart,
      end: state.dispTimeStart + state.dispTimeWin,
      channels: visibleChannels,
      bandLow: state.filterEnabled ? state.filterBandLow : undefined,
      bandHigh: state.filterEnabled ? state.filterBandHigh : undefined,
    },
    visibleChannels.length > 0,
  );

  const dr = meta ? 0.7 * (meta.amplitude_range.max - meta.amplitude_range.min) || 1 : 1;

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
  }, [windowData, state.dispChansNum, state.dispWaveMul, state.dispTimeStart, state.dispTimeWin, dr, markers]);

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

  if (!meta) {
    return (
      <Group justify="center" p="xl">
        <Loader size="sm" />
      </Group>
    );
  }

  return (
    <Group align="flex-start" gap="md" wrap="nowrap">
      <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
        <canvas
          ref={canvasRef}
          width={CANVAS_WIDTH}
          height={CANVAS_HEIGHT}
          style={{
            width: "100%",
            height: CANVAS_HEIGHT,
            background: "#fafafa",
            cursor: onCanvasClick ? "crosshair" : "default",
          }}
          onWheel={handleWheel}
          onClick={handleClick}
        />
        <Text size="xs" c="dimmed">
          {state.dispTimeStart.toFixed(1)}s &ndash; {(state.dispTimeStart + state.dispTimeWin).toFixed(1)}s (scroll to pan)
        </Text>
      </Stack>
      <Stack w={220} gap="sm">
        <EegToolbar state={state} dispatch={dispatch} />
        <EegChannelList
          channels={allChannels}
          excludedChannels={state.excludedChannels}
          onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
        />
      </Stack>
    </Group>
  );
}
