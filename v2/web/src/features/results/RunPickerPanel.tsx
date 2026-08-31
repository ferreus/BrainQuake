import { Badge, Button, Checkbox, Group, ScrollArea, Table, Text, Tooltip } from "@mantine/core";
import { ActionIcon } from "@mantine/core";
import { IconTrash } from "@tabler/icons-react";
import type { AnalysisRun } from "../../api/queries/useAnalysis";

interface RunPickerPanelProps {
  runs: AnalysisRun[];
  selected: Set<number>;
  onChange: (next: Set<number>) => void;
  onDelete: (run: AnalysisRun) => void;
}

const PROCESS_COLOR: Record<string, string> = {
  ei: "grape",
  hfo: "teal",
  fragility: "indigo",
};

/**
 * Every finished analysis run on this subject, across all processes. Ticking
 * rows is how the fused score is scoped: tick one and fusing shows that run on
 * its own, tick several and they average per process.
 */
export function RunPickerPanel({ runs, selected, onChange, onDelete }: RunPickerPanelProps) {
  if (runs.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        No finished analysis runs yet. Compute EI, HFO or fragility on the Analysis tab.
      </Text>
    );
  }

  function toggle(artifactId: number, checked: boolean) {
    const next = new Set(selected);
    if (checked) next.add(artifactId);
    else next.delete(artifactId);
    onChange(next);
  }

  const allChecked = runs.every((r) => selected.has(r.artifactId));

  return (
    <ScrollArea.Autosize mah={260}>
      <Table highlightOnHover fz="xs" stickyHeader>
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={28}>
              <Checkbox
                size="xs"
                checked={allChecked}
                indeterminate={!allChecked && selected.size > 0}
                onChange={(e) =>
                  onChange(e.currentTarget.checked ? new Set(runs.map((r) => r.artifactId)) : new Set())
                }
              />
            </Table.Th>
            <Table.Th>Run</Table.Th>
            <Table.Th ta="right">Chans</Table.Th>
            <Table.Th ta="right">R²</Table.Th>
            <Table.Th w={70}></Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {runs.map((r) => (
            <Table.Tr key={r.artifactId}>
              <Table.Td>
                <Checkbox
                  size="xs"
                  checked={selected.has(r.artifactId)}
                  onChange={(e) => toggle(r.artifactId, e.currentTarget.checked)}
                />
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <Badge size="xs" variant="light" color={PROCESS_COLOR[r.process] ?? "gray"}>
                    {r.process}
                  </Badge>
                  <div style={{ minWidth: 0 }}>
                    <Text fz="xs" truncate>
                      {r.label}
                    </Text>
                    <Text fz={10} c="dimmed" truncate>
                      {r.recording}
                    </Text>
                  </div>
                </Group>
              </Table.Td>
              <Table.Td ta="right">{r.nChannels}</Table.Td>
              <Table.Td ta="right">
                {r.medianR2 == null ? (
                  "—"
                ) : (
                  // Below 0.8 the linear fit is describing noise, so the run's
                  // ranking should not be fused without a second look.
                  <Text span fz="xs" c={r.medianR2 < 0.8 ? "orange" : undefined}>
                    {r.medianR2.toFixed(2)}
                  </Text>
                )}
              </Table.Td>
              <Table.Td>
                <Group gap={2} wrap="nowrap" justify="flex-end">
                  <Tooltip label="Select only this run" withArrow>
                    <Button
                      size="compact-xs"
                      variant="subtle"
                      onClick={() => onChange(new Set([r.artifactId]))}
                    >
                      only
                    </Button>
                  </Tooltip>
                  <Tooltip label="Delete this result" withArrow>
                    <ActionIcon size="sm" variant="subtle" color="red" onClick={() => onDelete(r)}>
                      <IconTrash size={13} />
                    </ActionIcon>
                  </Tooltip>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea.Autosize>
  );
}
