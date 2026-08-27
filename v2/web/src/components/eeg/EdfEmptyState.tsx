import { useState } from "react";
import { Button, FileButton, Paper, Progress, Stack, Text } from "@mantine/core";
import { IconHeartRateMonitor } from "@tabler/icons-react";
import type { Artifact } from "../../api/types";
import { useEdfUpload } from "./useEdfUpload";

interface EdfEmptyStateProps {
  subjectId: number;
  recordings: Artifact[];
  onUploaded: (edfArtifactId: number) => void;
}

/** Shown instead of the recording bar when a subject has no EDF yet: a small
 * "Import .edf" button in a dense toolbar was too easy to miss as the only way
 * to get data into the tab. */
export function EdfEmptyState({ subjectId, recordings, onUploaded }: EdfEmptyStateProps) {
  const { upload, uploadProgress } = useEdfUpload(subjectId, recordings, onUploaded);
  const [dragging, setDragging] = useState(false);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) upload(file);
  }

  return (
    <Paper
      withBorder
      p="xl"
      radius="sm"
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      style={{ borderStyle: "dashed", borderColor: dragging ? "var(--mantine-color-blue-6)" : undefined }}
    >
      <Stack align="center" gap="xs">
        <IconHeartRateMonitor size={40} stroke={1.2} opacity={0.5} />
        <Text fw={600}>No EEG recording for this subject</Text>
        <Text size="sm" c="dimmed">
          Drop an .edf file here, or import one to review it.
        </Text>
        <FileButton onChange={upload} accept=".edf">
          {(props) => (
            <Button size="md" {...props} loading={uploadProgress != null}>
              Import .edf
            </Button>
          )}
        </FileButton>
        {uploadProgress != null && <Progress w="100%" value={uploadProgress * 100} size="sm" animated />}
      </Stack>
    </Paper>
  );
}
