import { useEffect, useMemo, useRef, useState } from "react";
import { Group, Loader, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useDeleteEdfRecording, useEdfMeta } from "../../api/queries/useEdf";
import { useRecordingParams } from "../../api/queries/useRecordingParams";
import { AnnotationsPanel } from "../../components/eeg/AnnotationsPanel";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
import { EdfRecordingBar } from "../../components/eeg/EdfRecordingBar";
import { EegCanvas } from "../../components/eeg/EegCanvas";
import { EegChannelList } from "../../components/eeg/EegChannelList";
import { EegToolbar } from "../../components/eeg/EegToolbar";
import { useEegViewerState } from "../../components/eeg/useEegViewerState";
import { useBaselineTargetSelection } from "../../components/eeg/BaselineTargetLayer";
import { EiComputeForm } from "./EiComputeForm";
import { EiResultPanel } from "./EiResultPanel";

interface IctalPageProps {
  subjectId: number;
}

export function IctalPage({ subjectId }: IctalPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const edfArtifacts = (artifacts ?? []).filter((a) => a.kind === "raw_edf");
  const [selectedEdfId, setSelectedEdfId] = useState<number | undefined>();

  const effectiveEdfId = edfArtifacts.some((a) => a.id === selectedEdfId) ? selectedEdfId : edfArtifacts[0]?.id;

  const {
    data: meta,
    isLoading: metaLoading,
    isError: metaIsError,
    error: metaError,
    refetch: refetchMeta,
  } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState("ictal");
  const { data: recordingParams } = useRecordingParams(subjectId, effectiveEdfId);
  const selection = useBaselineTargetSelection();
  // Channels deleted in the list must leave the EI computation, not just the
  // plot -- see EiComputeForm's remainChannels prop.
  const remainChannels = useMemo(
    () => (meta?.channels ?? []).filter((c) => !state.excludedChannels.has(c)),
    [meta, state.excludedChannels],
  );
  const deleteRecording = useDeleteEdfRecording(subjectId);

  // Auxiliary traces out of the working set as soon as the recording loads.
  // They are ranked as if they were contacts -- a REF channel placed 6th in the
  // run behind docs/bella_ictal_ei_vs_annotation_discrepancy.md -- and they sit
  // in the common-average reference that every remaining channel is measured
  // against. Restorable from the channel list.
  useEffect(() => {
    if (!meta || effectiveEdfId == null) return;
    dispatch({ type: "LOAD_RECORDING", edfArtifactId: effectiveEdfId, auxChannels: meta.aux_channels ?? [] });
  }, [meta, effectiveEdfId, dispatch]);

  // Baseline/target are times into one specific recording, so they must not
  // survive a switch to another one -- carried over, they stay drawn on the new
  // trace and are what Compute EI would submit for it.
  const selectionEdfId = useRef(effectiveEdfId);
  const resetSelection = selection.reset;
  useEffect(() => {
    if (selectionEdfId.current === effectiveEdfId) return;
    selectionEdfId.current = effectiveEdfId;
    resetSelection();
  }, [effectiveEdfId, resetSelection]);

  // Seeds baseline/target/mains from this recording's last-submitted ictal
  // params once they've loaded -- guarded so it only fires once per
  // recording, not on every refetch (e.g. the invalidate right after a
  // Compute submit, which would otherwise stomp the values just entered).
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

  const markers = [
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
      ? [{ time: selection.pendingStart, color: selection.awaitingClick?.startsWith("baseline") ? "#1baf7a" : "#eb6834" }]
      : []),
  ];

  return (
    <Stack h="100%" gap="sm" mt="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        <EdfRecordingBar
          subjectId={subjectId}
          recordings={edfArtifacts}
          value={effectiveEdfId}
          onChange={setSelectedEdfId}
          onUploaded={() => selection.reset()}
        />
        {effectiveEdfId && meta && <EegToolbar state={state} dispatch={dispatch} />}
      </Group>

      {!effectiveEdfId && <Text c="dimmed">Import an ictal EDF recording to get started.</Text>}

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

      {effectiveEdfId && meta && (
        <>
          <Group align="flex-start" gap="md" wrap="nowrap">
            <EegCanvas
              subjectId={subjectId}
              edfArtifactId={effectiveEdfId}
              state={state}
              dispatch={dispatch}
              markers={markers}
              onCanvasClick={selection.awaitingClick ? selection.handleClick : undefined}
            />
            <Stack w={300} gap="sm">
              <EegChannelList
                channels={meta.channels}
                excludedChannels={state.excludedChannels}
                onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
                onRestore={(chs) => dispatch({ type: "RESTORE_CHANNELS", channels: chs })}
              />
              <AnnotationsPanel annotations={recordingParams?.annotations ?? []} onJumpTo={handleJumpToAnnotation} />
              <EiComputeForm
                subjectId={subjectId}
                edfArtifactId={effectiveEdfId}
                selection={selection}
                sfreq={meta.fs}
                remainChannels={remainChannels}
                mainsFreq={state.mainsFreq}
                initialParams={recordingParams?.ictal_params ?? null}
              />
            </Stack>
          </Group>
          <EiResultPanel subjectId={subjectId} edfArtifactId={effectiveEdfId} />
        </>
      )}
    </Stack>
  );
}
