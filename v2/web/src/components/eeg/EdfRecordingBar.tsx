import { Button, FileButton, Group, NativeSelect, Progress, Stack } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { useDeleteEdfRecording } from "../../api/queries/useEdf";
import type { Artifact } from "../../api/types";
import { edfDisplayName } from "../../lib/edfName";
import { useEdfUpload } from "./useEdfUpload";

interface EdfRecordingBarProps {
  subjectId: number;
  recordings: Artifact[];
  value: number | undefined;
  onChange: (edfArtifactId: number | undefined) => void;
  /** Fires after an upload finishes, with the new recording already selected. */
  onUploaded?: (edfArtifactId: number) => void;
}

/** Recording picker + import/delete, shared by the ictal, interictal and
 * clinical tabs (all list the same raw_edf artifacts). */
export function EdfRecordingBar({ subjectId, recordings, value, onChange, onUploaded }: EdfRecordingBarProps) {
  const deleteRecording = useDeleteEdfRecording(subjectId);
  const selected = recordings.find((a) => a.id === value);
  const { upload, uploadProgress } = useEdfUpload(subjectId, recordings, (id) => {
    onChange(id);
    onUploaded?.(id);
  });

  function handleDelete() {
    if (!selected) return;
    const name = edfDisplayName(selected);
    if (!confirm(`Delete recording "${name}"? Its EI/HFO jobs and results are deleted too.`)) return;
    deleteRecording.mutate(selected.id, {
      onSuccess: (result) => {
        onChange(undefined);
        notifications.show({
          title: `Deleted "${name}"`,
          message: `${result.deleted_artifacts} file(s) and ${result.deleted_jobs} job(s) removed`,
        });
      },
      onError: (err) => {
        notifications.show({
          color: "red",
          title: "Failed to delete recording",
          message: err instanceof ApiError ? err.message : String(err),
        });
      },
    });
  }

  return (
    <Stack gap={4}>
      <Group align="flex-end" gap="xs" wrap="nowrap">
        <NativeSelect
          label="EDF recording"
          data={recordings.map((a) => ({ value: String(a.id), label: edfDisplayName(a) }))}
          value={value != null ? String(value) : ""}
          onChange={(e) => onChange(Number(e.currentTarget.value))}
          disabled={recordings.length === 0}
        />
        <FileButton onChange={upload} accept=".edf">
          {(props) => (
            <Button size="xs" variant="default" {...props} disabled={uploadProgress != null}>
              Import .edf
            </Button>
          )}
        </FileButton>
        <Button
          size="xs"
          variant="light"
          color="red"
          onClick={handleDelete}
          disabled={!selected}
          loading={deleteRecording.isPending}
        >
          Delete
        </Button>
      </Group>
      {uploadProgress != null && <Progress value={uploadProgress * 100} size="sm" animated />}
    </Stack>
  );
}
