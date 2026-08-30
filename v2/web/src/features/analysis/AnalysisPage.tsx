import { useEffect, useMemo, useRef, useState } from "react";
import { Group, Loader, SegmentedControl, Select, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useDeleteEdfRecording, useEdfMeta } from "../../api/queries/useEdf";
import { useHfoResult } from "../../api/queries/useInterictal";
import { useRecordingParams } from "../../api/queries/useRecordingParams";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { AnnotationsPanel } from "../../components/eeg/AnnotationsPanel";
import { useBaselineTargetSelection } from "../../components/eeg/BaselineTargetLayer";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
import { EdfRecordingBar } from "../../components/eeg/EdfRecordingBar";
import { EegCanvas } from "../../components/eeg/EegCanvas";
import { EegChannelList } from "../../components/eeg/EegChannelList";
import { EegToolbar } from "../../components/eeg/EegToolbar";
import { useEegViewerState } from "../../components/eeg/useEegViewerState";
import {
  ANALYSIS_PROCESSES,
  DEFAULT_PROCESS_ID,
  findProcess,
  processProduces,
} from "./processes";

interface AnalysisPageProps {
  subjectId: number;
}

/**
 * The per-recording analysis screen, driven by a process registry.
 *
 * Replaces the near-identical IctalPage and InterictalPage: they differed only
 * in which compute form and result panel they mounted, which band seeded the
 * display filter, and whether they drew markers or event overlays. All four are
 * now declared in processes.tsx, so a new method is an entry there rather than
 * a seventh tab.
 */
export function AnalysisPage({ subjectId }: AnalysisPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const edfArtifacts = (artifacts ?? []).filter((a) => a.kind === "raw_edf");
  const [selectedEdfId, setSelectedEdfId] = useState<number | undefined>();
  const [processId, setProcessId] = useState(DEFAULT_PROCESS_ID);
  const [subTab, setSubTab] = useState("viewer");
  const [showOverlay, setShowOverlay] = useState(false);

  const process = findProcess(processId);
  const effectiveEdfId = edfArtifacts.some((a) => a.id === selectedEdfId) ? selectedEdfId : edfArtifacts[0]?.id;

  const {
    data: meta,
    isLoading: metaLoading,
    isError: metaIsError,
    error: metaError,
    refetch: refetchMeta,
  } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState(process.viewerDefaults);
  const { data: recordingParams } = useRecordingParams(subjectId, effectiveEdfId);
  const selection = useBaselineTargetSelection();
  const deleteRecording = useDeleteEdfRecording(subjectId);

  // Queried unconditionally so hook order stays stable across a process switch;
  // only read when the active process declares an `events` output.
  const { data: hfoResult } = useHfoResult(subjectId, effectiveEdfId, true);
  const hasEvents = processProduces(process, "events");

  // Channels deleted in the list must leave the computation, not just the plot:
  // they otherwise stay in the ranking AND in the common-average reference that
  // every remaining channel is measured against.
  const remainChannels = useMemo(
    () => (meta?.channels ?? []).filter((c) => !state.excludedChannels.has(c)),
    [meta, state.excludedChannels],
  );

  // Auxiliary traces out of the working set as soon as the recording loads. A
  // mark word (no unit) or a DC input (mV against microvolt contacts) is ranked
  // as if it were a contact and swamps the common average. Restorable from the
  // channel list.
  useEffect(() => {
    if (!meta || effectiveEdfId == null) return;
    dispatch({ type: "LOAD_RECORDING", edfArtifactId: effectiveEdfId, auxChannels: meta.aux_channels ?? [] });
  }, [meta, effectiveEdfId, dispatch]);

  // Switching process re-seeds the display band. Same two bands the two former
  // tabs hardcoded, now taken from the process declaration.
  const bandProcessId = useRef(processId);
  useEffect(() => {
    if (bandProcessId.current === processId) return;
    bandProcessId.current = processId;
    dispatch({
      type: "SET_FILTER_BAND",
      low: process.viewerDefaults.filterBandLow,
      high: process.viewerDefaults.filterBandHigh,
    });
    setSubTab("viewer");
  }, [processId, process.viewerDefaults, dispatch]);

  // Baseline/target are times into one specific recording, so they must not
  // survive a switch to another one -- carried over, they stay drawn on the new
  // trace and are what Compute would submit for it.
  const selectionEdfId = useRef(effectiveEdfId);
  const resetSelection = selection.reset;
  useEffect(() => {
    if (selectionEdfId.current === effectiveEdfId) return;
    selectionEdfId.current = effectiveEdfId;
    resetSelection();
  }, [effectiveEdfId, resetSelection]);

  // Seeds baseline/target/mains from this recording's last-submitted ictal
  // params once they've loaded -- guarded so it only fires once per recording,
  // not on every refetch (e.g. the invalidate right after a Compute submit,
  // which would otherwise stomp the values just entered).
  const seededParamsEdfId = useRef<number | undefined>(undefined);
  const setBaselineRange = selection.setBaselineRange;
  const setTargetRange = selection.setTargetRange;
  useEffect(() => {
    if (!recordingParams || recordingParams.edf_artifact_id !== effectiveEdfId) return;
    if (seededParamsEdfId.current === effectiveEdfId) return;
    seededParamsEdfId.current = effectiveEdfId;
    const p = recordingParams.ictal_params;
    if (!p) return;
    setBaselineRange([p.baseline_start, p.baseline_end]);
    setTargetRange([p.target_start, p.target_end]);
    if (p.mains_freq != null) dispatch({ type: "SET_MAINS_FREQ", value: p.mains_freq });
  }, [recordingParams, effectiveEdfId, setBaselineRange, setTargetRange, dispatch]);

  // channel name -> detected [start,end] events, consumed by EegCanvas' overlay.
  const eventOverlays = useMemo(() => {
    if (!hasEvents || !showOverlay || !hfoResult) return undefined;
    const map: Record<string, [number, number][]> = {};
    hfoResult.chn_names.forEach((name, i) => {
      map[name] = hfoResult.event_times[i] ?? [];
    });
    return map;
  }, [hasEvents, showOverlay, hfoResult]);

  const markers = useMemo(() => {
    if (!process.inputs.some((s) => s.type === "range" && !s.optional)) return [];
    return [
      ...(selection.baselineRange
        ? [
            { time: selection.baselineRange[0], color: "#1baf7a" },
            { time: selection.baselineRange[1], color: "#1baf7a" },
          ]
        : []),
      ...(selection.targetRange
        ? [
            { time: selection.targetRange[0], color: "#eb6834" },
            { time: selection.targetRange[1], color: "#eb6834" },
          ]
        : []),
      // Draw the first click's line right away, before the closing click lands.
      ...(selection.pendingStart != null
        ? [{
            time: selection.pendingStart,
            color: selection.awaitingClick?.startsWith("baseline") ? "#1baf7a" : "#eb6834",
          }]
        : []),
    ];
  }, [process, selection.baselineRange, selection.targetRange, selection.pendingStart, selection.awaitingClick]);

  function handleJumpToAnnotation(onset: number) {
    dispatch({ type: "SET_TIME_START", value: Math.max(0, onset - state.dispTimeWin / 2) });
  }

  function handleRemoveBadEdf() {
    if (!effectiveEdfId) return;
    deleteRecording.mutate(effectiveEdfId, {
      onSuccess: () => setSelectedEdfId(undefined),
      onError: (err) => {
        notifications.show({
          color: "red",
          title: "Failed to remove EDF",
          message: err instanceof ApiError ? err.message : String(err),
        });
      },
    });
  }

  const paneProps = meta && effectiveEdfId
    ? {
        subjectId,
        edfArtifactId: effectiveEdfId,
        meta,
        state,
        dispatch,
        remainChannels,
        recordingParams,
        selection,
      }
    : null;

  return (
    <Stack h="100%" gap="sm" pt="sm" style={{ minHeight: 0, overflow: "hidden" }}>
      <Group align="flex-end" gap="md" wrap="wrap">
        <EdfRecordingBar
          subjectId={subjectId}
          recordings={edfArtifacts}
          value={effectiveEdfId}
          onChange={setSelectedEdfId}
          onUploaded={() => selection.reset()}
        />
        <Select
          size="xs"
          w={220}
          label="Process"
          value={processId}
          onChange={(v) => v && setProcessId(v)}
          data={ANALYSIS_PROCESSES.map((p) => ({ value: p.id, label: p.label }))}
          allowDeselect={false}
        />
        {effectiveEdfId && meta && (
          <SegmentedControl
            size="xs"
            value={subTab}
            onChange={setSubTab}
            data={[
              { label: "Viewer", value: "viewer" },
              { label: process.resultTabLabel, value: "result" },
            ]}
          />
        )}
        {effectiveEdfId && meta && subTab === "viewer" && <EegToolbar state={state} dispatch={dispatch} />}
        {effectiveEdfId && meta && subTab === "viewer" && hasEvents && hfoResult && (
          <Switch
            label="Show HFO detections"
            size="sm"
            checked={showOverlay}
            onChange={(e) => setShowOverlay(e.currentTarget.checked)}
          />
        )}
      </Group>

      {!effectiveEdfId && <Text c="dimmed">Import an EDF recording to get started.</Text>}

      {effectiveEdfId && metaLoading && (
        <Group justify="center" p="xl">
          <Loader size="sm" />
        </Group>
      )}

      {effectiveEdfId && metaIsError && (
        <EdfLoadErrorPanel
          title="Failed to load EDF recording"
          error={metaError}
          onRetry={() => refetchMeta()}
          onRemove={handleRemoveBadEdf}
          removing={deleteRecording.isPending}
        />
      )}

      {paneProps && meta && effectiveEdfId && (
        <>
          {/* Both sub-views stay mounted (display-toggled) so the canvas pan
              position and form state survive flipping between them. */}
          <Group
            align="stretch"
            gap="md"
            wrap="nowrap"
            style={{ flex: 1, minHeight: 0, display: subTab === "viewer" ? "flex" : "none" }}
          >
            <EegCanvas
              subjectId={subjectId}
              edfArtifactId={effectiveEdfId}
              state={state}
              dispatch={dispatch}
              markers={markers}
              eventOverlays={eventOverlays}
              onCanvasClick={selection.awaitingClick ? selection.handleClick : undefined}
            />
            <Stack w={300} gap="sm" h="100%" style={{ flexShrink: 0, overflowY: "auto" }}>
              <CollapsibleSection
                title="Channels"
                badge={`${remainChannels.length}/${meta.channels.length}`}
              >
                <EegChannelList
                  channels={meta.channels}
                  excludedChannels={state.excludedChannels}
                  onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
                  onRestore={(chs) => dispatch({ type: "RESTORE_CHANNELS", channels: chs })}
                />
              </CollapsibleSection>
              <AnnotationsPanel annotations={recordingParams?.annotations ?? []} onJumpTo={handleJumpToAnnotation} />
              <CollapsibleSection title={process.computeTitle}>
                <process.ParamsForm {...paneProps} />
              </CollapsibleSection>
            </Stack>
          </Group>
          <div
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
              flexDirection: "column",
              display: subTab === "result" ? "flex" : "none",
            }}
          >
            <process.ResultView {...paneProps} />
          </div>
        </>
      )}
    </Stack>
  );
}
