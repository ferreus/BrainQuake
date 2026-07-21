import { useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useComputeEi } from "../../api/queries/useIctal";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";
import type { BaselineTargetSelection } from "../../components/eeg/BaselineTargetLayer";

interface EiComputeFormProps {
  subjectId: number;
  edfArtifactId: number;
  selection: BaselineTargetSelection;
}

/**
 * The EI computation's own bandpass (default 1-500Hz broadband) is separate
 * from the trace-display filter shown in the EEG canvas toolbar (default
 * 60-140Hz for ictal) -- the legacy Qt client keeps these two filters
 * distinct too (api_client.py's compute_ei always sent 1.0/500.0 regardless
 * of what the display filter fields showed).
 */
export function EiComputeForm({ subjectId, edfArtifactId, selection }: EiComputeFormProps) {
  const [bandLow, setBandLow] = useState(1.0);
  const [bandHigh, setBandHigh] = useState(500.0);
  const [jobId, setJobId] = useState<number | undefined>();

  const computeEi = useComputeEi(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["ei-result", subjectId, edfArtifactId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "EI computation failed", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleCompute() {
    if (!selection.baselineRange || !selection.targetRange) return;
    try {
      const j = await computeEi.mutateAsync({
        edfArtifactId,
        params: {
          baseline_start: selection.baselineRange[0],
          baseline_end: selection.baselineRange[1],
          target_start: selection.targetRange[0],
          target_end: selection.targetRange[1],
          band_low: bandLow,
          band_high: bandHigh,
        },
      });
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start EI computation",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;
  const ready = selection.baselineRange != null && selection.targetRange != null;

  return (
    <Paper withBorder p="sm" w={300}>
      <Title order={6} mb="xs">
        Compute EI
      </Title>
      <Group gap={4} mb={4}>
        <Button
          size="xs"
          variant={selection.awaitingClick?.startsWith("baseline") ? "filled" : "default"}
          onClick={selection.startBaselineSelect}
        >
          Set Baseline
        </Button>
        <Button
          size="xs"
          variant={selection.awaitingClick?.startsWith("target") ? "filled" : "default"}
          onClick={selection.startTargetSelect}
        >
          Set Target
        </Button>
      </Group>
      <Text size="xs" c="dimmed">
        Baseline:{" "}
        {selection.baselineRange
          ? `${selection.baselineRange[0].toFixed(2)}s - ${selection.baselineRange[1].toFixed(2)}s`
          : "not set"}
      </Text>
      <Text size="xs" c="dimmed" mb="xs">
        Target:{" "}
        {selection.targetRange ? `${selection.targetRange[0].toFixed(2)}s - ${selection.targetRange[1].toFixed(2)}s` : "not set"}
      </Text>
      {selection.awaitingClick && (
        <Text size="xs" c="blue" mb="xs">
          Click on the trace to mark {selection.awaitingClick.replace("-", " ")}.
        </Text>
      )}
      <NumberInput label="Band low (Hz)" value={bandLow} onChange={(v) => setBandLow(Number(v) || 0)} size="xs" />
      <NumberInput label="Band high (Hz)" value={bandHigh} onChange={(v) => setBandHigh(Number(v) || 0)} size="xs" mt={4} />
      <Button size="xs" mt="sm" fullWidth loading={running} disabled={!ready} onClick={handleCompute}>
        Compute EI
      </Button>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}
