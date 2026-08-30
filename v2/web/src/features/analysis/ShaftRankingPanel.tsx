import { useMemo, useState } from "react";
import {
  Badge, Group, NumberInput, Paper, Table, Text, TextInput, Title, Tooltip,
} from "@mantine/core";
import { useAnalysisAggregate } from "../../api/queries/useAnalysis";

interface ShaftRankingPanelProps {
  subjectId: number;
  process: string;
}

function parseShafts(text: string): Set<string> {
  return new Set(text.split(",").map((s) => s.trim()).filter(Boolean));
}

/**
 * The cross-seizure ranking -- the actual finding. A single seizure's ranking
 * swings too much to report, so votes are pooled across every finished run and
 * divided by each shaft's contact count.
 *
 * The clinical shaft fields only annotate the table, exactly as run_frag.R's
 * --onset-shafts/--spread-shafts do; they change nothing that is computed.
 */
export function ShaftRankingPanel({ subjectId, process }: ShaftRankingPanelProps) {
  const [topN, setTopN] = useState(20);
  const [onsetShafts, setOnsetShafts] = useState("");
  const [spreadShafts, setSpreadShafts] = useState("");
  const { data, isLoading, isError } = useAnalysisAggregate(subjectId, process, topN);

  const onset = useMemo(() => parseShafts(onsetShafts), [onsetShafts]);
  const spread = useMemo(() => parseShafts(spreadShafts), [spreadShafts]);

  if (isLoading) return null;
  if (isError || !data) return null;

  const lowR2 = data.runs.filter((r) => r.median_r2 != null && r.median_r2 < 0.8);

  return (
    <Paper withBorder p="sm" mt="sm">
      <Group gap="xs" mb="xs" wrap="nowrap">
        <Title order={6}>Shaft ranking across {data.n_runs} run{data.n_runs === 1 ? "" : "s"}</Title>
        {data.n_runs === 1 && (
          <Badge size="xs" color="orange" variant="light">
            n=1 &mdash; not a finding
          </Badge>
        )}
      </Group>

      {data.n_runs === 0 ? (
        <Text size="xs" c="dimmed">
          No finished runs yet. Select several seizures and run the process; this table fills in
          as each one completes.
        </Text>
      ) : (
        <>
          <Group gap="xs" align="flex-end" mb="xs">
            <NumberInput
              size="xs" w={110} label="Top-N vote"
              value={topN} onChange={(v) => setTopN(Math.max(1, Number(v) || 1))} min={1}
            />
            <TextInput
              size="xs" w={140} label="Clinical onset shafts" placeholder="A, I"
              value={onsetShafts} onChange={(e) => setOnsetShafts(e.currentTarget.value)}
            />
            <TextInput
              size="xs" w={140} label="Early spread shafts" placeholder="N, P, G"
              value={spreadShafts} onChange={(e) => setSpreadShafts(e.currentTarget.value)}
            />
          </Group>

          <Table striped highlightOnHover withTableBorder fz="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Shaft</Table.Th>
                <Table.Th ta="right">Contacts</Table.Th>
                <Table.Th ta="right">Votes</Table.Th>
                <Table.Th ta="right">Votes/contact</Table.Th>
                <Table.Th>Clinical</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.shafts.filter((s) => s.votes > 0).map((s) => (
                <Table.Tr key={s.shaft}>
                  <Table.Td fw={600}>{s.shaft}</Table.Td>
                  <Table.Td ta="right">{s.n_contacts}</Table.Td>
                  <Table.Td ta="right">{s.votes}</Table.Td>
                  <Table.Td ta="right">{s.votes_per_channel.toFixed(2)}</Table.Td>
                  <Table.Td>
                    {onset.has(s.shaft) && (
                      <Badge size="xs" color="red" variant="light">EEG onset</Badge>
                    )}
                    {spread.has(s.shaft) && (
                      <Badge size="xs" color="orange" variant="light">early spread</Badge>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <Text size="xs" fw={500} mt="sm">
            Per-run fit quality
          </Text>
          {lowR2.length > 0 && (
            <Text size="xs" c="orange" mt={2}>
              {lowR2.length} run{lowR2.length === 1 ? " has" : "s have"} a median R&sup2; below 0.8
              &mdash; those rankings describe noise more than dynamics.
            </Text>
          )}
          <Group gap={6} mt={4}>
            {data.runs.map((r) => (
              <Tooltip
                key={r.job_id}
                withArrow
                label={
                  `${r.recording}${r.label ? ` -- ${r.label}` : ""}` +
                  `${r.onset_s != null ? ` @ ${r.onset_s.toFixed(1)}s` : ""}` +
                  ` -- ${r.n_channels} channels`
                }
              >
                <Badge
                  size="xs"
                  variant="light"
                  color={r.median_r2 == null ? "gray" : r.median_r2 < 0.8 ? "orange" : "green"}
                >
                  {(r.label ?? r.recording) +
                    (r.median_r2 == null ? "" : `  R² ${r.median_r2.toFixed(3)}`)}
                </Badge>
              </Tooltip>
            ))}
          </Group>
        </>
      )}
    </Paper>
  );
}
