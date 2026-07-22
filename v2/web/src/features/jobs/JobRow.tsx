import { ActionIcon, Badge, Group, Progress, Table, Text, Tooltip } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconFileText, IconRefresh, IconTrash, IconX } from "@tabler/icons-react";
import { ApiError } from "../../api/client";
import type { Job, JobState } from "../../api/types";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { useCancelJob, useDeleteJob, useRetryJob } from "../../api/queries/useJobs";

const STATE_COLORS: Record<JobState, string> = {
  queued: "gray",
  running: "blue",
  finished: "green",
  failed: "red",
  cancelled: "orange",
};

interface JobRowProps {
  job: Job;
  onViewLog: (jobId: number) => void;
}

export function JobRow({ job, onViewLog }: JobRowProps) {
  const cancelJob = useCancelJob();
  const retryJob = useRetryJob();
  const deleteJob = useDeleteJob();
  const isTerminal = TERMINAL_JOB_STATES.has(job.state);
  const canRetry = job.state === "failed" || job.state === "cancelled";

  function handleRetry() {
    retryJob.mutate(job, {
      onError: (err) =>
        notifications.show({
          color: "red",
          title: "Failed to retry job",
          message: err instanceof ApiError ? err.message : String(err),
        }),
    });
  }

  function handleDelete() {
    deleteJob.mutate(job.id, {
      onError: (err) =>
        notifications.show({
          color: "red",
          title: "Failed to delete job",
          message: err instanceof ApiError ? err.message : String(err),
        }),
    });
  }

  return (
    <Table.Tr>
      <Table.Td>{job.id}</Table.Td>
      <Table.Td>{job.job_type}</Table.Td>
      <Table.Td>
        <Badge color={STATE_COLORS[job.state]} variant="light">
          {job.state}
        </Badge>
      </Table.Td>
      <Table.Td style={{ minWidth: 160 }}>
        {job.state === "running" ? (
          <Tooltip label={job.progress_message ?? ""}>
            <Progress value={job.progress_pct} size="sm" animated />
          </Tooltip>
        ) : (
          <Text size="xs" c="dimmed" lineClamp={1}>
            {job.progress_message ?? "—"}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Group gap={4} wrap="nowrap">
          <Tooltip label="View log">
            <ActionIcon size="sm" variant="subtle" onClick={() => onViewLog(job.id)}>
              <IconFileText size={16} />
            </ActionIcon>
          </Tooltip>
          {canRetry && (
            <Tooltip label="Retry">
              <ActionIcon size="sm" variant="subtle" loading={retryJob.isPending} onClick={handleRetry}>
                <IconRefresh size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          {!isTerminal && (
            <Tooltip label="Cancel job">
              <ActionIcon
                size="sm"
                color="red"
                variant="subtle"
                loading={cancelJob.isPending}
                onClick={() => cancelJob.mutate(job.id)}
              >
                <IconX size={16} />
              </ActionIcon>
            </Tooltip>
          )}
          {isTerminal && (
            <Tooltip label="Delete job">
              <ActionIcon
                size="sm"
                color="red"
                variant="subtle"
                loading={deleteJob.isPending}
                onClick={handleDelete}
              >
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}
