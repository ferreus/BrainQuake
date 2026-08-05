import { useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { useDetectElectrodes } from "../../api/queries/useElectrodes";
import { useJobRunner } from "../../api/queries/useJobRunner";
import { usePrefillFromLastJob } from "../../api/queries/usePrefillFromLastJob";
import { qk } from "../../api/queryKeys";
import type { DetectParams } from "../../api/endpoints";

interface DetectFormProps {
  subjectId: number;
  disabled: boolean;
  detected: boolean;
}

export function DetectForm({ subjectId, disabled, detected }: DetectFormProps) {
  const [K, setK] = useState(10);
  const [thresholdPct, setThresholdPct] = useState(8);
  const [erosionIterations, setErosionIterations] = useState(13);

  const detectMutation = useDetectElectrodes(subjectId);

  usePrefillFromLastJob<DetectParams>(subjectId, "elec_detect", (p) => {
    if (p.K != null) setK(p.K);
    if (p.threshold_pct != null) setThresholdPct(p.threshold_pct);
    if (p.erosion_iterations != null) setErosionIterations(p.erosion_iterations);
  });

  const { run, job, running } = useJobRunner({
    start: () =>
      detectMutation.mutateAsync({ K, threshold_pct: thresholdPct, erosion_iterations: erosionIterations }),
    failTitle: "Electrode detection failed",
    startFailTitle: "Failed to start detection",
    invalidate: [qk.artifacts(subjectId)],
  });

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
        <Button size="xs" loading={running} disabled={disabled} onClick={() => run()}>
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
