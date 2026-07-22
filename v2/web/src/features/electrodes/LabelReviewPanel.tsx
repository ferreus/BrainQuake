import { Button, Checkbox, Group, Paper, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { useLabelsSummary, useUpdateLabels } from "../../api/queries/useElectrodes";

interface LabelReviewPanelProps {
  subjectId: number;
  excluded: Set<number>;
  onExcludedChange: (excluded: Set<number>) => void;
}

/**
 * Whole-cluster accept/reject review of the GMM clusters produced by
 * detect(), before segment() walks each cluster into named contacts. The Qt
 * client's server API already supported PUT .../labels {exclude_labels}, but
 * its own UI never surfaced a way to pick which clusters to exclude -- this
 * is the first client to build that. Reads a cheap server-computed summary
 * (voxel count + centroid per cluster) rather than shipping the full 256^3
 * label volume to the browser. `excluded` is lifted up to ElectrodesPage so
 * the ClusterCentroids 3D preview can dim the same clusters this table has
 * unchecked.
 */
export function LabelReviewPanel({ subjectId, excluded, onExcludedChange }: LabelReviewPanelProps) {
  const { data, isLoading } = useLabelsSummary(subjectId, true);
  const updateLabels = useUpdateLabels(subjectId);

  function toggle(label: number) {
    const next = new Set(excluded);
    if (next.has(label)) next.delete(label);
    else next.add(label);
    onExcludedChange(next);
  }

  async function handleCommit() {
    try {
      await updateLabels.mutateAsync(Array.from(excluded));
      onExcludedChange(new Set());
      notifications.show({
        color: "green",
        title: "Labels updated",
        message: "Excluded clusters dropped and remaining ones renumbered.",
      });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to update labels",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  if (isLoading || !data) return null;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        2. Review Clusters ({data.K})
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        Uncheck any cluster that looks like noise (too few voxels, off in empty space) before segmenting.
      </Text>
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Keep</Table.Th>
            <Table.Th>Label</Table.Th>
            <Table.Th>Voxels</Table.Th>
            <Table.Th>Centroid</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {data.clusters.map((c) => (
            <Table.Tr key={c.label}>
              <Table.Td>
                <Checkbox checked={!excluded.has(c.label)} onChange={() => toggle(c.label)} />
              </Table.Td>
              <Table.Td>{c.label}</Table.Td>
              <Table.Td>{c.voxel_count}</Table.Td>
              <Table.Td>{c.centroid.map((v) => v.toFixed(0)).join(", ")}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group justify="flex-end" mt="sm">
        <Button size="xs" loading={updateLabels.isPending} disabled={excluded.size === 0} onClick={handleCommit}>
          {excluded.size > 0 ? `Exclude ${excluded.size} cluster(s)` : "Exclude"}
        </Button>
      </Group>
    </Paper>
  );
}
