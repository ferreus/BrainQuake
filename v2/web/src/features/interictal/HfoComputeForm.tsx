import { useState } from "react";
import { Button, NumberInput, Paper, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useComputeHfo } from "../../api/queries/useInterictal";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface HfoComputeFormProps {
  subjectId: number;
  edfArtifactId: number;
  /** HFO/ripple band -- taken from the trace-display filter (default 80-250Hz
   * for interictal), matching client_inter.py which reused self.band_low/high. */
  bandLow: number;
  bandHigh: number;
  /** Channels not deleted in the channel list; sent as remain_chns so the
   * server only scans the same set the user is looking at. */
  remainChannels: string[];
}

/**
 * Queues an HFO (high-frequency-events) detection job and polls it. Thresholds
 * mirror client_inter.py's four line-edits (rel/abs envelope thresholds, min
 * gap to merge, min duration to keep). On finish it invalidates the hfo-result
 * query so HiResultPanel and the canvas overlay pick up the detections.
 */
export function HfoComputeForm({ subjectId, edfArtifactId, bandLow, bandHigh, remainChannels }: HfoComputeFormProps) {
  const [relThresh, setRelThresh] = useState(2.0);
  const [absThresh, setAbsThresh] = useState(2.0);
  const [minGap, setMinGap] = useState(20.0);
  const [minLast, setMinLast] = useState(50.0);
  const [jobId, setJobId] = useState<number | undefined>();

  const computeHfo = useComputeHfo(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["hfo-result", subjectId, edfArtifactId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "HFO computation failed", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleCompute() {
    try {
      const j = await computeHfo.mutateAsync({
        edfArtifactId,
        params: {
          band_low: bandLow,
          band_high: bandHigh,
          rel_thresh: relThresh,
          abs_thresh: absThresh,
          min_gap: minGap,
          min_last: minLast,
          remain_chns: remainChannels,
        },
      });
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start HFO computation",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <Paper withBorder p="sm" w={300}>
      <Title order={6} mb="xs">
        Compute HFO (High-frequency events)
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        Band {bandLow}&ndash;{bandHigh} Hz (from display filter), {remainChannels.length} channels.
      </Text>
      <NumberInput label="Rel. threshold (× channel median)" value={relThresh} onChange={(v) => setRelThresh(Number(v) || 0)} size="xs" step={0.5} />
      <NumberInput label="Abs. threshold (× global median)" value={absThresh} onChange={(v) => setAbsThresh(Number(v) || 0)} size="xs" step={0.5} mt={4} />
      <NumberInput label="Min gap (ms, merge events)" value={minGap} onChange={(v) => setMinGap(Number(v) || 0)} size="xs" mt={4} />
      <NumberInput label="Min duration (ms, keep event)" value={minLast} onChange={(v) => setMinLast(Number(v) || 0)} size="xs" mt={4} />
      <Button size="xs" mt="sm" fullWidth loading={running} onClick={handleCompute}>
        Compute HFO
      </Button>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}
