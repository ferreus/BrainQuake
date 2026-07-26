import { useMemo, useState } from "react";
import { Button, FileButton, Group, Loader, NativeSelect, Progress, Stack, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, uploadFileWithProgress } from "../../api/client";
import { useArtifacts, useDeleteArtifact } from "../../api/queries/useElectrodes";
import { useEdfMeta } from "../../api/queries/useEdf";
import { useHfoResult } from "../../api/queries/useInterictal";
import { EdfLoadErrorPanel } from "../../components/eeg/EdfLoadErrorPanel";
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
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [showOverlay, setShowOverlay] = useState(false);
  const queryClient = useQueryClient();

  const effectiveEdfId = selectedEdfId ?? edfArtifacts[0]?.id;

  const {
    data: meta,
    isLoading: metaLoading,
    isError: metaIsError,
    error: metaError,
    refetch: refetchMeta,
  } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState("interictal");
  const deleteArtifact = useDeleteArtifact(subjectId);

  const { data: hfoResult } = useHfoResult(subjectId, effectiveEdfId, true);

  const remainChannels = useMemo(
    () => (meta?.channels ?? []).filter((c) => !state.excludedChannels.has(c)),
    [meta, state.excludedChannels],
  );

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
    deleteArtifact.mutate(effectiveEdfId, {
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

  async function handleUpload(file: File | null) {
    if (!file) return;
    setUploadProgress(0);
    try {
      const artifact = await uploadFileWithProgress<{ id: number }>(
        `/subjects/${subjectId}/upload`,
        file,
        "edf",
        setUploadProgress,
      ).promise;
      queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
      setSelectedEdfId(artifact.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to upload EDF",
        message: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setUploadProgress(null);
    }
  }

  return (
    <Stack h="100%" gap="sm" mt="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        <NativeSelect
          label="EDF recording"
          data={edfArtifacts.map((a) => ({
            value: String(a.id),
            label: (a.meta_json?.original_filename as string) ?? `#${a.id}`,
          }))}
          value={effectiveEdfId ? String(effectiveEdfId) : ""}
          onChange={(e) => setSelectedEdfId(Number(e.currentTarget.value))}
          disabled={edfArtifacts.length === 0}
        />
        <FileButton onChange={handleUpload} accept=".edf">
          {(props) => (
            <Button size="xs" variant="default" {...props}>
              Import .edf
            </Button>
          )}
        </FileButton>
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
      {uploadProgress != null && <Progress value={uploadProgress * 100} size="sm" animated />}

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
          removing={deleteArtifact.isPending}
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
              eventOverlays={eventOverlays}
            />
            <Stack w={300} gap="sm">
              <EegChannelList
                channels={meta.channels}
                excludedChannels={state.excludedChannels}
                onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
              />
              <HfoComputeForm
                subjectId={subjectId}
                edfArtifactId={effectiveEdfId}
                bandLow={state.filterBandLow}
                bandHigh={state.filterBandHigh}
                remainChannels={remainChannels}
              />
            </Stack>
          </Group>
          <HiResultPanel subjectId={subjectId} edfArtifactId={effectiveEdfId} />
        </>
      )}
    </Stack>
  );
}
