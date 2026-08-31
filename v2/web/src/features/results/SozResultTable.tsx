import { ScrollArea, Table, Text } from "@mantine/core";
import { interpolatePlasma } from "d3-scale-chromatic";
import type { SozResultRow } from "../../api/endpoints";

interface SozResultTableProps {
  rows: SozResultRow[];
  /** Fused processes, in column order -- see the server's fused_processes(). */
  processes: string[];
}

function num(v: unknown, digits: number): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : "—";
}

/** Ranked contact table: one score + percentile column per fused process, then
 * the combined score. Rows arrive pre-sorted by combined_score desc; the leading
 * swatch is the same plasma color used for that contact in 3D. */
export function SozResultTable({ rows, processes }: SozResultTableProps) {
  return (
    <ScrollArea h="100%">
      <Table stickyHeader highlightOnHover fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={28}></Table.Th>
            <Table.Th>Contact</Table.Th>
            {processes.map((p) => (
              <Table.Th key={p} ta="right">
                {p}
              </Table.Th>
            ))}
            <Table.Th ta="right">Combined</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.contact}>
              <Table.Td>
                <div
                  style={{
                    width: 14,
                    height: 14,
                    borderRadius: 3,
                    background: interpolatePlasma(Math.min(1, Math.max(0, r.combined_score))),
                  }}
                />
              </Table.Td>
              <Table.Td>{r.contact}</Table.Td>
              {processes.map((p) => (
                // The percentile is what fuses; the raw mean is shown beneath it
                // because raw EI, HFO counts and fragility are not comparable.
                <Table.Td key={p} ta="right">
                  <Text span fz="xs" c={r[`suspect_${p}`] ? "red" : undefined}>
                    {num(r[`${p}_percentile`], 2)}
                  </Text>
                  <Text fz={10} c="dimmed">
                    {num(r[p], p === "hfo" ? 0 : 3)}
                  </Text>
                </Table.Td>
              ))}
              <Table.Td ta="right">{num(r.combined_score, 3)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}
