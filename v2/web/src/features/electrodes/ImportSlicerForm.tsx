import { useState } from "react";
import { Button, FileButton, Group, Paper, Progress, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { uploadMrb } from "../../api/endpoints";
import { useStartSlicerMrbPreview } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface ImportSlicerFormProps {
  subjectId: number;
  disabled: boolean;
  hasPendingPreview: boolean;
}

/**
 * Uploads a raw 3D Slicer .mrb scene and starts the slicer_mrb_parse job that
 * turns it into a reviewable preview (see SlicerImportReviewPanel below it in
 * the sidebar) -- bypasses Register CT / Detect / Segment entirely. Parsing,
 * node/transform-direction auto-selection, and the in-brain sanity check all
 * happen server-side (services/electrodes.parse_mrb); nothing is written to
 * the real contacts until the preview is explicitly approved. See
 * docs/seeg_slicer_contact_import_plan.md.
 */
export function ImportSlicerForm({ subjectId, disabled, hasPendingPreview }: ImportSlicerFormProps) {
  const [jobId, setJobId] = useState<number | undefined>();
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const startPreview = useStartSlicerMrbPreview(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    queryClient.invalidateQueries({ queryKey: ["slicer-mrb-preview", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "Failed to parse .mrb", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleFile(file: File | null) {
    if (!file) return;
    setUploadProgress(0);
    try {
      const artifact = await uploadMrb(subjectId, file, setUploadProgress).promise;
      setUploadProgress(null);
      const j = await startPreview.mutateAsync(artifact.id);
      setJobId(j.id);
    } catch (err) {
      setUploadProgress(null);
      notifications.show({
        color: "red",
        title: "Failed to start import",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running =
    uploadProgress != null || startPreview.isPending || (job ? !TERMINAL_JOB_STATES.has(job.state) : false);

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        Import from 3D Slicer
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        {disabled
          ? "Run brain reconstruction first -- needed for the surface-RAS conversion and the in-brain sanity check."
          : hasPendingPreview
            ? "A parsed preview is waiting for review below."
            : "Upload a .mrb scene exported from 3D Slicer's SEEG module."}
      </Text>
      <Group justify="flex-end">
        <FileButton onChange={handleFile} accept=".mrb" disabled={disabled || running}>
          {(props) => (
            <Button size="xs" loading={running} disabled={disabled} {...props}>
              Import from 3D Slicer
            </Button>
          )}
        </FileButton>
      </Group>
      {uploadProgress != null && <Progress value={uploadProgress * 100} size="xs" mt={4} animated />}
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}
