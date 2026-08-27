import { useEffect, useMemo, useState } from "react";
import { Group, Loader, Stack } from "@mantine/core";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useEdfMeta } from "../../api/queries/useEdf";
import { useRecordingParams } from "../../api/queries/useRecordingParams";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { AnnotationList } from "../../components/eeg/AnnotationsPanel";
import { EdfEmptyState } from "../../components/eeg/EdfEmptyState";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
import { EdfRecordingBar } from "../../components/eeg/EdfRecordingBar";
import { midnightSeconds } from "../../lib/eegTime";
import { ClinicalCanvas } from "./ClinicalCanvas";
import { ClinicalChannelList } from "./ClinicalChannelList";
import { ClinicalTimeBar } from "./ClinicalTimeBar";
import { ClinicalToolbar } from "./ClinicalToolbar";
import { useClinicalViewState } from "./useClinicalViewState";

interface ClinicalEegPageProps {
  subjectId: number;
}

/** Read-only clinical review of a recording, at Nihon Kohden settings.
 *
 * Intentionally has no compute forms, markers or result overlays: this tab
 * exists so review settings can be changed freely without touching what the EI
 * and HFO jobs run on. The reference for what it should look like is
 * v2/tools/show_edf.py. */
export function ClinicalEegPage({ subjectId }: ClinicalEegPageProps) {
  const { data: artifacts, isLoading: artifactsLoading } = useArtifacts(subjectId);
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
  const { state, dispatch } = useClinicalViewState();
  const { data: recordingParams } = useRecordingParams(subjectId, effectiveEdfId);
  const annotations = useMemo(() => recordingParams?.annotations ?? [], [recordingParams]);
  const clockOrigin = useMemo(() => midnightSeconds(meta?.meas_date), [meta]);
  const [cursorTime, setCursorTime] = useState<number | null>(null);

  useEffect(() => {
    if (!meta || effectiveEdfId == null) return;
    dispatch({
      type: "LOAD_RECORDING",
      edfArtifactId: effectiveEdfId,
      channels: meta.channels,
      auxChannels: meta.aux_channels ?? [],
      durationSec: meta.duration_sec,
    });
  }, [meta, effectiveEdfId, dispatch]);

  function handleJumpToAnnotation(onset: number) {
    dispatch({ type: "SET_TIME_START", value: Math.max(0, onset - state.pageSeconds / 2) });
  }

  // Held back until the list has actually loaded -- flashing the empty state
  // over a subject that does have recordings reads as data loss.
  if (artifactsLoading) {
    return (
      <Group justify="center" p="xl">
        <Loader size="sm" />
      </Group>
    );
  }

  if (edfArtifacts.length === 0) {
    return (
      <Stack gap="sm" h="100%" style={{ flex: 1, minHeight: 0 }}>
        <EdfEmptyState subjectId={subjectId} recordings={edfArtifacts} onUploaded={setSelectedEdfId} />
      </Stack>
    );
  }

  return (
    <Stack gap="sm" h="100%" style={{ flex: 1, minHeight: 0 }}>
      <EdfRecordingBar
        subjectId={subjectId}
        recordings={edfArtifacts}
        value={effectiveEdfId}
        onChange={setSelectedEdfId}
        onUploaded={setSelectedEdfId}
      />

      {effectiveEdfId && metaLoading && (
        <Group justify="center" p="xl">
          <Loader size="sm" />
        </Group>
      )}

      {effectiveEdfId && metaIsError && (
        <EdfLoadErrorPanel title="Failed to load EDF recording" error={metaError} onRetry={() => refetchMeta()} />
      )}

      {effectiveEdfId && meta && (
        <>
          <ClinicalToolbar state={state} dispatch={dispatch} />
          <ClinicalTimeBar
            state={state}
            dispatch={dispatch}
            clockOrigin={clockOrigin}
            cursorTime={cursorTime}
          />
          <Group align="stretch" gap="md" wrap="nowrap" style={{ flex: 1, minHeight: 0 }}>
            <ClinicalCanvas
              subjectId={subjectId}
              edfArtifactId={effectiveEdfId}
              state={state}
              dispatch={dispatch}
              annotations={annotations}
              onCursorTimeChange={setCursorTime}
            />
            <Stack w={300} gap="sm" h="100%" style={{ flexShrink: 0, overflowY: "auto" }}>
              <CollapsibleSection
                title="Channel selection"
                badge={`${state.selectedChannels.size}/${meta.channels.length}`}
              >
                <ClinicalChannelList
                  channels={meta.channels}
                  auxChannels={meta.aux_channels ?? []}
                  selectedChannels={state.selectedChannels}
                  onChange={(chs) => dispatch({ type: "SET_SELECTED_CHANNELS", channels: chs })}
                />
              </CollapsibleSection>
              <CollapsibleSection title="Annotations" badge={annotations.length}>
                <AnnotationList
                  annotations={annotations}
                  onJumpTo={handleJumpToAnnotation}
                  clockOrigin={clockOrigin}
                  highlightRange={[state.timeStart, state.timeStart + state.pageSeconds]}
                />
              </CollapsibleSection>
            </Stack>
          </Group>
        </>
      )}
    </Stack>
  );
}
