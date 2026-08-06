import { Alert, Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import {
  useApproveSlicerMrbPreview,
  useRejectSlicerMrbPreview,
  useSlicerMrbPreview,
} from "../../api/queries/useElectrodes";

interface SlicerImportReviewPanelProps {
  subjectId: number;
}

const LOW_CONFIDENCE_THRESHOLD = 0.5;

/**
 * Review step for a pending 3D Slicer .mrb import. parse_mrb() (server-side)
 * auto-picks which markups node holds the real per-contact list and which
 * direction to apply its registration transform in -- both are heuristics
 * that can be wrong on an unfamiliar scene, so this panel surfaces exactly
 * what was picked plus the in-brain fraction (the sanity check used to pick
 * the transform direction) and requires an explicit Approve before any of it
 * is written into the real chnXyzDict/contact_txt. See
 * docs/seeg_slicer_contact_import_plan.md.
 */
export function SlicerImportReviewPanel({ subjectId }: SlicerImportReviewPanelProps) {
  const { data, isLoading } = useSlicerMrbPreview(subjectId, true);
  const approve = useApproveSlicerMrbPreview(subjectId);
  const reject = useRejectSlicerMrbPreview(subjectId);

  async function handleApprove() {
    try {
      const result = await approve.mutateAsync();
      notifications.show({
        color: "green",
        title: "Contacts imported",
        message: `${result.n_contacts} contacts across ${result.n_electrodes} electrodes.`,
      });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to approve",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  async function handleReject() {
    try {
      await reject.mutateAsync();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to reject",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  if (isLoading || !data) return null;
  const d = data.diagnostics;
  const lowConfidence = d.in_brain_fraction < LOW_CONFIDENCE_THRESHOLD;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        Review Slicer Import
      </Title>
      <Stack gap={4} mb="xs">
        <Text size="xs" c="dimmed">
          Node: <b>{d.node_name}</b>
          {d.candidate_node_names.length > 1 && ` (of ${d.candidate_node_names.length} candidates)`}
        </Text>
        <Text size="xs" c="dimmed">
          Transform direction: <b>{d.transform_used}</b>
        </Text>
        <Text size="xs" c={lowConfidence ? "red" : "dimmed"}>
          In-brain: <b>{(d.in_brain_fraction * 100).toFixed(0)}%</b> of {d.n_points} contacts across{" "}
          {d.n_electrodes} electrodes
        </Text>
      </Stack>
      {d.warnings.map((w) => (
        <Alert key={w} color="red" mb="xs" p="xs">
          <Text size="xs">{w}</Text>
        </Alert>
      ))}
      <Group justify="flex-end" gap="xs">
        <Button size="xs" color="red" variant="outline" loading={reject.isPending} onClick={handleReject}>
          Reject
        </Button>
        <Button size="xs" color="green" loading={approve.isPending} onClick={handleApprove}>
          Approve
        </Button>
      </Group>
    </Paper>
  );
}
