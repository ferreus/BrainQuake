import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch } from "react";
import { Group, Loader, Stack, Text } from "@mantine/core";
import { ApiError } from "../../api/client";
import { useEdfMeta, useEdfWindow } from "../../api/queries/useEdf";
import { useBipolarPreview } from "../../api/queries/useIctal";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
import { formatAxisTime, midnightSeconds } from "./clinicalTime";
import type { ClinicalViewAction, ClinicalViewState } from "./useClinicalViewState";

interface ClinicalCanvasProps {
  subjectId: number;
  edfArtifactId: number;
  state: ClinicalViewState;
  dispatch: Dispatch<ClinicalViewAction>;
}

const MIN_CANVAS_HEIGHT = 320;
const FALLBACK_CANVAS_SIZE = { width: 900, height: 600 };
/** Left gutter holding the channel names. Used by every x mapping in here --
 * the analysis canvas grew a click-vs-draw mismatch by applying it in one
 * place and not the other. */
const LABEL_WIDTH = 96;
/** Trace row spacing in mm, fitted to the NK display (show_edf.py's ROW_MM), so
 * one row spans sensitivity * ROW_MM uV. */
const ROW_MM = 12;

/**
 * Clinical review renderer: stacked traces at an absolute sensitivity, negative
 * up, on a wall-clock axis. Reproduces what v2/tools/show_edf.py draws through
 * MNE's browser.
 *
 * Separate from components/eeg/EegCanvas by design -- that one is wired into the
 * ictal/interictal analysis state, and this view must not touch it.
 */
export function ClinicalCanvas({ subjectId, edfArtifactId, state, dispatch }: ClinicalCanvasProps) {
  const { data: meta, isError: metaIsError, error: metaError, refetch: refetchMeta } = useEdfMeta(subjectId, edfArtifactId);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [canvasSize, setCanvasSize] = useState(FALLBACK_CANVAS_SIZE);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let timer: number | undefined;
    // Trailing debounce + same-size bail-out: panel toggles and window drags
    // fire bursts of resize events, and a full redraw per event blocks the
    // main thread.
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

  const selectedContacts = useMemo(
    () => (meta?.channels ?? []).filter((c) => state.selectedChannels.has(c)),
    [meta, state.selectedChannels],
  );

  // Under bipolar the addressable rows are derivations, not contacts. The
  // server already computes which pairs a contact set builds (bipolar-preview),
  // so the pairing rule stays in one place rather than being duplicated here.
  const bipolar = state.montage === "bipolar";
  const preview = useBipolarPreview(subjectId, edfArtifactId, selectedContacts, bipolar);
  // Memoised: a fresh array here would change params.channels' identity every
  // render, which resets useEdfWindow's debounce timer and never settles.
  const previewPairs = preview.data?.pairs;
  const rowNames = useMemo(
    () => (bipolar ? previewPairs ?? [] : selectedContacts),
    [bipolar, previewPairs, selectedContacts],
  );
  const visibleChannels = useMemo(
    () => rowNames.slice(state.dispChansStart, state.dispChansStart + state.dispChansNum),
    [rowNames, state.dispChansStart, state.dispChansNum],
  );

  const { data: windowData, isError: windowIsError, error: windowError } = useEdfWindow(
    subjectId,
    edfArtifactId,
    {
      start: state.timeStart,
      end: state.timeStart + state.pageSeconds,
      channels: visibleChannels,
      // tc is always sent so the traces stay referenced and notched even with
      // both filters off; 0 is how the endpoint spells "low cut off".
      tc: state.timeConstant ?? 0,
      bandHigh: state.highCut ?? undefined,
      mainsFreq: state.mainsFreq,
      reference: state.montage,
    },
    visibleChannels.length > 0,
  );

  const clockOrigin = useMemo(() => midnightSeconds(meta?.meas_date), [meta]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !windowData) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { width, height } = canvas;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);

    const winStart = state.timeStart;
    const winEnd = state.timeStart + state.pageSeconds;
    const plotWidth = width - LABEL_WIDTH;
    // Row pitch from the configured page size, not the returned channel count:
    // a short last page must keep the same uV per row, or the sensitivity the
    // toolbar claims is not the sensitivity on screen.
    const rowHeight = height / state.dispChansNum;
    const pixelsPerUv = rowHeight / (state.sensitivity * ROW_MM);
    const timeToX = (t: number) => LABEL_WIDTH + ((t - winStart) / (winEnd - winStart)) * plotWidth;

    ctx.font = "10px monospace";
    ctx.strokeStyle = "rgba(128,128,128,0.25)";
    ctx.fillStyle = "#888";
    const tickSeconds = state.pageSeconds <= 10 ? 1 : 5;
    for (let t = Math.ceil(winStart / tickSeconds) * tickSeconds; t <= winEnd; t += tickSeconds) {
      const x = timeToX(t);
      ctx.beginPath();
      ctx.moveTo(x, 12);
      ctx.lineTo(x, height);
      ctx.stroke();
      ctx.fillText(formatAxisTime(t, clockOrigin), x + 2, 10);
    }

    for (let i = 0; i < windowData.channels.length; i++) {
      const y = i * rowHeight + rowHeight / 2;
      ctx.strokeStyle = "rgba(128,128,128,0.18)";
      ctx.beginPath();
      ctx.moveTo(LABEL_WIDTH, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.fillStyle = "#555";
      ctx.fillText(windowData.channels[i], 4, y - 3);
    }

    // Black on white, the clinical convention -- deliberately not theme-aware.
    ctx.strokeStyle = "#111";
    ctx.lineWidth = 1;
    const polarity = state.negativeUp ? 1 : -1;
    windowData.channels.forEach((_, rowIndex) => {
      const samples = windowData.data[rowIndex];
      if (!samples || samples.length === 0) return;
      const rowCenter = rowIndex * rowHeight + rowHeight / 2;
      ctx.beginPath();
      for (let i = 0; i < samples.length; i++) {
        const x = LABEL_WIDTH + (i / Math.max(1, samples.length - 1)) * plotWidth;
        const y = rowCenter + polarity * samples[i] * 1e6 * pixelsPerUv;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });
  }, [windowData, state.sensitivity, state.dispChansNum, state.timeStart, state.pageSeconds,
      state.negativeUp, clockOrigin, canvasSize]);

  function handleWheel(e: React.WheelEvent<HTMLCanvasElement>) {
    e.preventDefault();
    dispatch({ type: "PAN_TIME", direction: e.deltaY > 0 ? 1 : -1 });
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

  const rowMicrovolts = state.sensitivity * ROW_MM;
  return (
    <Stack gap={4} style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
      <div ref={containerRef} style={{ flex: 1, minHeight: MIN_CANVAS_HEIGHT }}>
        <canvas
          ref={canvasRef}
          width={canvasSize.width}
          height={canvasSize.height}
          style={{ display: "block", width: "100%", height: "100%", background: "#fff" }}
          onWheel={handleWheel}
        />
      </div>
      {windowIsError ? (
        <Text size="xs" c="red">
          Failed to load this window: {windowError instanceof ApiError ? windowError.message : String(windowError)}
        </Text>
      ) : (
        <Text size="xs" c="dimmed">
          {formatAxisTime(state.timeStart, clockOrigin)} &ndash;{" "}
          {formatAxisTime(state.timeStart + state.pageSeconds, clockOrigin)} · {state.sensitivity} µV/mm (
          {rowMicrovolts} µV per row) · TC {state.timeConstant == null ? "off" : `${state.timeConstant} s`} · HC{" "}
          {state.highCut == null ? "off" : `${state.highCut} Hz`} · {rowNames.length} rows · scroll to pan
        </Text>
      )}
    </Stack>
  );
}
