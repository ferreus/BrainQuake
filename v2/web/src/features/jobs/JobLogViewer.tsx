import { Button, Group, Modal, ScrollArea, Text } from "@mantine/core";
import { IconDownload } from "@tabler/icons-react";
import { useJobLog } from "../../api/queries/useJobs";

interface JobLogViewerProps {
  jobId: number | null;
  onClose: () => void;
}

function downloadLog(jobId: number, log: string) {
  const blob = new Blob([log], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `job_${jobId}.log`;
  a.click();
  URL.revokeObjectURL(url);
}

export function JobLogViewer({ jobId, onClose }: JobLogViewerProps) {
  const { data: log, isLoading, isError } = useJobLog(jobId ?? undefined, jobId != null);

  return (
    <Modal opened={jobId != null} onClose={onClose} title={`Job ${jobId ?? ""} log`} size="lg">
      <Group justify="flex-end" mb="xs">
        <Button
          leftSection={<IconDownload size={16} />}
          variant="default"
          size="xs"
          disabled={!log}
          onClick={() => jobId != null && log && downloadLog(jobId, log)}
        >
          Download log
        </Button>
      </Group>
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
