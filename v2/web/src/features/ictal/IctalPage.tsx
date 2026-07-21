import { useState } from "react";
import { Button, FileButton, Group, NativeSelect, Progress, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, uploadFileWithProgress } from "../../api/client";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useEdfMeta } from "../../api/queries/useEdf";
import { EegCanvas } from "../../components/eeg/EegCanvas";
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
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const queryClient = useQueryClient();

  const effectiveEdfId = selectedEdfId ?? edfArtifacts[0]?.id;

  const { data: meta } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState("ictal");
  const selection = useBaselineTargetSelection();

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
      selection.reset();
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
  ];

  return (
    <Stack h="100%" gap="sm" mt="md">
      <Group justify="space-between">
        <Group>
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
              <Button size="xs" variant="default" mt={22} {...props}>
                Import .edf
              </Button>
            )}
          </FileButton>
        </Group>
      </Group>
      {uploadProgress != null && <Progress value={uploadProgress * 100} size="sm" animated />}

      {!effectiveEdfId && <Text c="dimmed">Import an ictal EDF recording to get started.</Text>}

      {effectiveEdfId && meta && (
        <>
          <EegCanvas
            subjectId={subjectId}
            edfArtifactId={effectiveEdfId}
            state={state}
            dispatch={dispatch}
            markers={markers}
            onCanvasClick={selection.awaitingClick ? selection.handleClick : undefined}
          />
          <Group align="flex-start" gap="md" wrap="nowrap">
            <EiComputeForm subjectId={subjectId} edfArtifactId={effectiveEdfId} selection={selection} />
            <EiResultPanel
              subjectId={subjectId}
              edfArtifactId={effectiveEdfId}
              targetRange={selection.targetRange}
              bandLow={state.filterBandLow}
              bandHigh={state.filterBandHigh}
            />
          </Group>
        </>
      )}
    </Stack>
  );
}
