import { useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { useSegmentElectrodes } from "../../api/queries/useElectrodes";
import { useJobRunner } from "../../api/queries/useJobRunner";
import { usePrefillFromLastJob } from "../../api/queries/usePrefillFromLastJob";
import { qk } from "../../api/queryKeys";
import type { SegmentParams } from "../../api/endpoints";

interface SegmentFormProps {
  subjectId: number;
  disabled: boolean;
  segmented: boolean;
}

export function SegmentForm({ subjectId, disabled, segmented }: SegmentFormProps) {
  const [numMax, setNumMax] = useState(20);
  const [diameterSize, setDiameterSize] = useState(2.5);
  const [spacing, setSpacing] = useState(2.5);

  const segmentMutation = useSegmentElectrodes(subjectId);

  usePrefillFromLastJob<SegmentParams>(subjectId, "elec_segment", (p) => {
    if (p.numMax != null) setNumMax(p.numMax);
    if (p.diameterSize != null) setDiameterSize(p.diameterSize);
    if (p.spacing != null) setSpacing(p.spacing);
  });

  const { run, job, running } = useJobRunner({
    start: () => segmentMutation.mutateAsync({ numMax, diameterSize, spacing }),
    failTitle: "Segmentation failed",
    startFailTitle: "Failed to start segmentation",
    invalidate: [qk.artifacts(subjectId), qk.chnXyz(subjectId)],
  });

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
        <Button size="xs" loading={running} disabled={disabled} onClick={() => run()}>
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
