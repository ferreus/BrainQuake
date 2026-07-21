import { useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useSegmentElectrodes } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface SegmentFormProps {
  subjectId: number;
  disabled: boolean;
  segmented: boolean;
}

export function SegmentForm({ subjectId, disabled, segmented }: SegmentFormProps) {
  const [numMax, setNumMax] = useState(20);
  const [diameterSize, setDiameterSize] = useState(2.5);
  const [spacing, setSpacing] = useState(2.5);
  const [jobId, setJobId] = useState<number | undefined>();

  const segmentMutation = useSegmentElectrodes(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    queryClient.invalidateQueries({ queryKey: ["chn-xyz", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({
        color: "red",
        title: "Segmentation failed",
        message: finishedJob.progress_message ?? "",
      });
    }
  });

  async function handleSegment() {
    try {
      const j = await segmentMutation.mutateAsync({ numMax, diameterSize, spacing });
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start segmentation",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        3. Segment Contacts
      </Title>
      <NumberInput
        label="Max contacts per shaft"
        value={numMax}
        onChange={(v) => setNumMax(Number(v) || 0)}
        min={1}
        disabled={disabled || running}
      />
      <NumberInput
        label="Contact diameter (voxels)"
        value={diameterSize}
        onChange={(v) => setDiameterSize(Number(v) || 0)}
        step={0.5}
        disabled={disabled || running}
        mt="xs"
      />
      <NumberInput
        label="Inter-contact spacing (voxels)"
        value={spacing}
        onChange={(v) => setSpacing(Number(v) || 0)}
        step={0.5}
        disabled={disabled || running}
        mt="xs"
      />
      <Group justify="space-between" mt="sm">
        <Text size="xs" c="dimmed">
          {disabled ? "Detect electrodes first" : segmented ? "Already segmented -- rerun to redo" : ""}
        </Text>
        <Button size="xs" loading={running} disabled={disabled} onClick={handleSegment}>
          Segment
        </Button>
      </Group>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}
