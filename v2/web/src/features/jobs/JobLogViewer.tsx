import { Modal, ScrollArea, Text } from "@mantine/core";
import { useJobLog } from "../../api/queries/useJobs";

interface JobLogViewerProps {
  jobId: number | null;
  onClose: () => void;
}

export function JobLogViewer({ jobId, onClose }: JobLogViewerProps) {
  const { data: log, isLoading, isError } = useJobLog(jobId ?? undefined, jobId != null);

  return (
    <Modal opened={jobId != null} onClose={onClose} title={`Job ${jobId ?? ""} log`} size="lg">
      <ScrollArea h={400}>
        {isLoading && <Text c="dimmed">Loading log...</Text>}
        {isError && <Text c="red">Log not available yet.</Text>}
        {log && (
          <Text component="pre" size="xs" style={{ whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
            {log}
          </Text>
        )}
      </ScrollArea>
    </Modal>
  );
}
