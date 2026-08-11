import { useEffect, useMemo, useState } from "react";
import { Group, Loader, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useDeleteEdfRecording, useEdfMeta } from "../../api/queries/useEdf";
import { useHfoResult } from "../../api/queries/useInterictal";
import { useRecordingParams } from "../../api/queries/useRecordingParams";
import { AnnotationsPanel } from "../../components/eeg/AnnotationsPanel";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
import { EdfRecordingBar } from "../../components/eeg/EdfRecordingBar";
import { EegCanvas } from "../../components/eeg/EegCanvas";
import { EegChannelList } from "../../components/eeg/EegChannelList";
import { EegToolbar } from "../../components/eeg/EegToolbar";
import { useEegViewerState } from "../../components/eeg/useEegViewerState";
import { HfoComputeForm } from "./HfoComputeForm";
import { HiResultPanel } from "./HiResultPanel";

interface InterictalPageProps {
  subjectId: number;
}

export function InterictalPage({ subjectId }: InterictalPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const edfArtifacts = (artifacts ?? []).filter((a) => a.kind === "raw_edf");
  const [selectedEdfId, setSelectedEdfId] = useState<number | undefined>();
  const [showOverlay, setShowOverlay] = useState(false);

  const effectiveEdfId = edfArtifacts.some((a) => a.id === selectedEdfId) ? selectedEdfId : edfArtifacts[0]?.id;

  const {
    data: meta,
    isLoading: metaLoading,
    isError: metaIsError,
    error: metaError,
    refetch: refetchMeta,
  } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState("interictal");
  const { data: recordingParams } = useRecordingParams(subjectId, effectiveEdfId);
  const deleteRecording = useDeleteEdfRecording(subjectId);

  const { data: hfoResult } = useHfoResult(subjectId, effectiveEdfId, true);

  function handleJumpToAnnotation(onset: number) {
    dispatch({ type: "SET_TIME_START", value: Math.max(0, onset - state.dispTimeWin / 2) });
  }

  const remainChannels = useMemo(
    () => (meta?.channels ?? []).filter((c) => !state.excludedChannels.has(c)),
    [meta, state.excludedChannels],
  );

  // The server classifies channels by the contact-naming convention and reports
  // the auxiliary ones; drop them from the working set as soon as the recording
  // loads. Sending them to the HFO job puts them in its common-average
  // reference, where a mark word (no unit, so read raw) or a DC input (mV
  // against microvolt contacts) swamps every contact and the detector returns
  // the same events on all of them. Restorable from the channel list.
  useEffect(() => {
    if (!meta || effectiveEdfId == null) return;
    dispatch({ type: "LOAD_RECORDING", edfArtifactId: effectiveEdfId, auxChannels: meta.aux_channels ?? [] });
  }, [meta, effectiveEdfId, dispatch]);

  // channel name -> detected [start,end] events, consumed by EegCanvas' overlay.
  const eventOverlays = useMemo(() => {
    if (!showOverlay || !hfoResult) return undefined;
    const map: Record<string, [number, number][]> = {};
    hfoResult.chn_names.forEach((name, i) => {
      map[name] = hfoResult.event_times[i] ?? [];
    });
    return map;
  }, [showOverlay, hfoResult]);

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

  return (
    <Stack h="100%" gap="sm" mt="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        <EdfRecordingBar
          subjectId={subjectId}
          recordings={edfArtifacts}
          value={effectiveEdfId}
          onChange={setSelectedEdfId}
        />
        {effectiveEdfId && meta && <EegToolbar state={state} dispatch={dispatch} />}
        {effectiveEdfId && meta && hfoResult && (
          <Switch
            label="Show HFO detections"
            size="sm"
            checked={showOverlay}
            onChange={(e) => setShowOverlay(e.currentTarget.checked)}
          />
        )}
      </Group>

      {!effectiveEdfId && <Text c="dimmed">Import an interictal EDF recording to get started.</Text>}

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
          <Group align="stretch" gap="md" wrap="nowrap" style={{ flex: 1, minHeight: 0 }}>
            <EegCanvas
              subjectId={subjectId}
              edfArtifactId={effectiveEdfId}
              state={state}
              dispatch={dispatch}
              eventOverlays={eventOverlays}
            />
            <Stack w={300} gap="sm" style={{ alignSelf: "flex-start" }}>
              <EegChannelList
                channels={meta.channels}
                excludedChannels={state.excludedChannels}
                onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
                onRestore={(chs) => dispatch({ type: "RESTORE_CHANNELS", channels: chs })}
              />
              <AnnotationsPanel annotations={recordingParams?.annotations ?? []} onJumpTo={handleJumpToAnnotation} />
              <HfoComputeForm
                subjectId={subjectId}
                edfArtifactId={effectiveEdfId}
                bandLow={state.filterBandLow}
                bandHigh={state.filterBandHigh}
                remainChannels={remainChannels}
                sfreq={meta.fs}
                durationSec={meta.duration_sec}
                initialParams={recordingParams?.interictal_params ?? null}
              />
            </Stack>
          </Group>
          <HiResultPanel subjectId={subjectId} edfArtifactId={effectiveEdfId} />
        </>
      )}
    </Stack>
  );
}
