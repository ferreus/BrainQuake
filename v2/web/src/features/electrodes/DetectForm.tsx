import { useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useDetectElectrodes } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface DetectFormProps {
  subjectId: number;
  disabled: boolean;
  detected: boolean;
}

export function DetectForm({ subjectId, disabled, detected }: DetectFormProps) {
  const [K, setK] = useState(8);
  const [thresholdPct, setThresholdPct] = useState(70);
  const [erosionIterations, setErosionIterations] = useState(2);
  const [jobId, setJobId] = useState<number | undefined>();

  const detectMutation = useDetectElectrodes(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({
        color: "red",
        title: "Electrode detection failed",
        message: finishedJob.progress_message ?? "",
      });
    }
  });

  async function handleDetect() {
    try {
      const j = await detectMutation.mutateAsync({
        K,
        threshold_pct: thresholdPct,
        erosion_iterations: erosionIterations,
      });
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start detection",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        1. Detect Electrodes
      </Title>
      <NumberInput
        label="Number of electrodes (K)"
        value={K}
        onChange={(v) => setK(Number(v) || 0)}
        min={1}
        disabled={disabled || running}
      />
      <NumberInput
        label="Threshold %"
        value={thresholdPct}
        onChange={(v) => setThresholdPct(Number(v) || 0)}
        min={0}
        max={100}
        disabled={disabled || running}
        mt="xs"
      />
      <NumberInput
        label="Erosion iterations"
        value={erosionIterations}
        onChange={(v) => setErosionIterations(Number(v) || 0)}
        min={0}
        disabled={disabled || running}
        mt="xs"
      />
      <Group justify="space-between" mt="sm">
        <Text size="xs" c="dimmed">
          {disabled ? "Register CT first" : detected ? "Already detected -- rerun to redo" : ""}
        </Text>
        <Button size="xs" loading={running} disabled={disabled} onClick={handleDetect}>
          Detect
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
