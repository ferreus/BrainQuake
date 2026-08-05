import { useState } from "react";
import { Button, NumberInput, Paper, Text, Title } from "@mantine/core";
import { useComputeHfo } from "../../api/queries/useInterictal";
import { useJobRunner } from "../../api/queries/useJobRunner";
import { qk } from "../../api/queryKeys";

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

  const computeHfo = useComputeHfo(subjectId);

  const { run, job, running } = useJobRunner({
    start: () =>
      computeHfo.mutateAsync({
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
      }),
    failTitle: "HFO computation failed",
    startFailTitle: "Failed to start HFO computation",
    invalidate: [qk.hfoResult(subjectId, edfArtifactId)],
  });

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
      <Button size="xs" mt="sm" fullWidth loading={running} onClick={() => run()}>
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
