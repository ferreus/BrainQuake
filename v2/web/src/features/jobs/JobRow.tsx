import { ActionIcon, Badge, Button, Group, Progress, Table, Text, Tooltip } from "@mantine/core";
import type { Job, JobState } from "../../api/types";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { useCancelJob } from "../../api/queries/useJobs";

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
  const isTerminal = TERMINAL_JOB_STATES.has(job.state);

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
          <Button size="xs" variant="subtle" onClick={() => onViewLog(job.id)}>
            Log
          </Button>
          {!isTerminal && (
            <ActionIcon
              size="sm"
              color="red"
              variant="subtle"
              loading={cancelJob.isPending}
              onClick={() => cancelJob.mutate(job.id)}
              title="Cancel job"
            >
              ✕
            </ActionIcon>
          )}
        </Group>
      </Table.Td>
    </Table.Tr>
  );
}
