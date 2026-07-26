import { ScrollArea, Table, Text } from "@mantine/core";
import { interpolatePlasma } from "d3-scale-chromatic";
import type { SozResultRow } from "../../api/endpoints";

interface SozResultTableProps {
  rows: SozResultRow[];
}

function fmt(v: number | null, digits: number): string {
  return v == null ? "—" : v.toFixed(digits);
}

/** Ranked contact table mirroring client_soz.py's QTableWidget (contact, EI,
 * HI, combined, suspect EI/HI). Rows arrive pre-sorted by combined_score desc;
 * the leading swatch is the same plasma color used for that contact in 3D. */
export function SozResultTable({ rows }: SozResultTableProps) {
  return (
    <ScrollArea h="100%">
      <Table stickyHeader highlightOnHover fz="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th w={28}></Table.Th>
            <Table.Th>Contact</Table.Th>
            <Table.Th ta="right">EI</Table.Th>
            <Table.Th ta="right">HI</Table.Th>
            <Table.Th ta="right">Combined</Table.Th>
            <Table.Th ta="center">Suspect (EI/HI)</Table.Th>
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
              <Table.Td ta="right">{fmt(r.ei, 3)}</Table.Td>
              <Table.Td ta="right">{fmt(r.hi, 0)}</Table.Td>
              <Table.Td ta="right">{fmt(r.combined_score, 3)}</Table.Td>
              <Table.Td ta="center">
                <Text span c={r.suspect_ei ? "red" : "dimmed"} fz="xs">
                  {r.suspect_ei ? "yes" : "no"}
                </Text>
                {" / "}
                <Text span c={r.suspect_hi ? "red" : "dimmed"} fz="xs">
                  {r.suspect_hi ? "yes" : "no"}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}
