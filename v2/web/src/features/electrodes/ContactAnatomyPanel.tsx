import { useMemo, useState } from "react";
import { Alert, Anchor, Group, Paper, ScrollArea, Table, Text, TextInput, Title, Tooltip } from "@mantine/core";
import { ApiError } from "../../api/client";
import { useContactAnatomy } from "../../api/queries/useContactAnatomy";
import type { ContactAnatomy } from "../../api/endpoints";
import { isInStructure } from "../../lib/contactAnatomy";

interface ContactAnatomyPanelProps {
  subjectId: number;
  /** Contact name selected in the 3D view, or null. */
  selected: string | null;
  onSelect: (name: string | null) => void;
}

function NearestCell({ contact }: { contact: ContactAnatomy }) {
  if (contact.out_of_volume) return <Text size="xs" c="red">outside volume</Text>;
  const nearest = contact.nearest_structure;
  if (!nearest) {
    // Nothing grey within the search radius -- deep white matter, and saying so
    // is the finding. An empty cell would read as missing data instead.
    return (
      <Text size="xs" c="dimmed">
        none within radius
      </Text>
    );
  }
  if (isInStructure(contact)) return <Text size="xs">—</Text>;
  return (
    <Text size="xs">
      {nearest.label_name} <Text span c="dimmed">{nearest.distance_mm.toFixed(1)} mm</Text>
    </Text>
  );
}

/**
 * Anatomical label for every contact, from services/anatomy.py. Rows select
 * the same contact the 3D view does (by contact name, not row index), so
 * clicking a sphere scrolls-to/highlights its row and vice versa.
 *
 * Shows the exact-voxel label and the nearest-structure fallback as separate
 * columns on purpose: SEEG contacts sit in white matter constantly, and
 * collapsing "in WM, 1.4 mm from hippocampus" into a single "hippocampus" is
 * exactly the overstatement this table exists to avoid.
 */
export function ContactAnatomyPanel({ subjectId, selected, onSelect }: ContactAnatomyPanelProps) {
  const { data, isLoading, error } = useContactAnatomy(subjectId);
  const [filter, setFilter] = useState("");

  const rows = useMemo(() => {
    const contacts = data?.contacts ?? [];
    const q = filter.trim().toLowerCase();
    if (!q) return contacts;
    return contacts.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        (c.label_name ?? "").toLowerCase().includes(q) ||
        (c.nearest_structure?.label_name ?? "").toLowerCase().includes(q),
    );
  }, [data, filter]);

  if (isLoading) return null;

  if (error) {
    // 404 here is the normal "no contacts yet" / "no recon yet" state, not a
    // failure worth a red alert -- the server's detail says which.
    const message = error instanceof ApiError ? error.message : String(error);
    return (
      <Paper withBorder p="sm">
        <Title order={6} mb="xs">
          Contact anatomy
        </Title>
        <Text size="xs" c="dimmed">
          {message}
        </Text>
      </Paper>
    );
  }

  if (!data) return null;

  const outOfVolume = data.contacts.filter((c) => c.out_of_volume).length;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        Contact anatomy ({data.contacts.length})
      </Title>
      <Text size="xs" c="dimmed" mb="xs">
        From <code>{data.segmentation}</code>, nearest structure searched within {data.radius_mm} mm. Click a row
        or a contact in the 3D view to select it.
      </Text>
      {data.segmentation === "mri/aseg.mgz" && (
        <Alert color="yellow" variant="light" p="xs" mb="xs">
          <Text size="xs">
            This subject has no cortical parcellation (aparc+aseg.mgz), so cortical contacts can only be named
            "Cerebral-Cortex". Re-run reconstruction to get parcel names.
          </Text>
        </Alert>
      )}
      {outOfVolume > 0 && (
        <Alert color="red" variant="light" p="xs" mb="xs">
          <Text size="xs">
            {outOfVolume} contact(s) fall outside the segmentation entirely — usually a coordinate-space error in
            the import, not anatomy.
          </Text>
        </Alert>
      )}
      <TextInput
        size="xs"
        mb="xs"
        placeholder="Filter by contact or structure"
        value={filter}
        onChange={(e) => setFilter(e.currentTarget.value)}
      />
      <ScrollArea.Autosize mah={320} type="auto">
        <Table highlightOnHover stickyHeader>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Contact</Table.Th>
              <Table.Th>At contact</Table.Th>
              <Table.Th>Nearest structure</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((c) => (
              <Table.Tr
                key={c.name}
                bg={c.name === selected ? "var(--mantine-color-red-light)" : undefined}
                style={{ cursor: "pointer" }}
                onClick={() => onSelect(c.name === selected ? null : c.name)}
              >
                <Table.Td>
                  <Anchor component="span" size="xs" fw={c.name === selected ? 700 : 400}>
                    {c.name}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <Tooltip
                    label={c.neighborhood
                      .map((n) => `${n.label_name} ${(n.fraction * 100).toFixed(0)}%`)
                      .join(" · ")}
                    disabled={c.neighborhood.length === 0}
                    multiline
                    w={260}
                    withArrow
                  >
                    <Text size="xs" c={isInStructure(c) ? undefined : "dimmed"}>
                      {c.label_name ?? "—"}
                    </Text>
                  </Tooltip>
                </Table.Td>
                <Table.Td>
                  <NearestCell contact={c} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </ScrollArea.Autosize>
      {rows.length === 0 && (
        <Group justify="center" p="xs">
          <Text size="xs" c="dimmed">
            No contact matches "{filter}".
          </Text>
        </Group>
      )}
    </Paper>
  );
}
