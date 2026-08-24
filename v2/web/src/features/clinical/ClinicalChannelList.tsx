import { useMemo, useState } from "react";
import { Button, Checkbox, Group, ScrollArea, Stack, Text, TextInput } from "@mantine/core";

interface ClinicalChannelListProps {
  channels: string[];
  auxChannels: string[];
  selectedChannels: Set<string>;
  onChange: (channels: string[]) => void;
}

// Full match, mirroring show_edf.py's CONTACT_RE: G'1 is a contact on shaft G',
// G'1-1 (a junk bank whose labels got "-1" suffixes to stay unique) is not.
const CONTACT_RE = /^([A-Za-z]+'?)\s*(\d+)$/;

function shaftOf(name: string): string | null {
  const m = CONTACT_RE.exec(name.replace("POL ", "").replace("EEG ", "").trim());
  return m ? m[1] : null;
}

/** Channel picker for the clinical view, grouped by electrode shaft -- the
 * review action is "show me G' and L'", not clicking twelve boxes. Mirrors
 * show_edf.py's SHAFTS argument.
 *
 * Display only: this selection reaches no computation, unlike the ictal /
 * interictal channel list which feeds remain_chns. */
export function ClinicalChannelList({ channels, auxChannels, selectedChannels, onChange }: ClinicalChannelListProps) {
  const [query, setQuery] = useState("");
  const aux = useMemo(() => new Set(auxChannels), [auxChannels]);

  const shafts = useMemo(() => {
    const groups = new Map<string, string[]>();
    for (const name of channels) {
      const key = shaftOf(name) ?? "Other";
      const group = groups.get(key);
      if (group) group.push(name);
      else groups.set(key, [name]);
    }
    return [...groups];
  }, [channels]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return channels;
    return channels.filter((name) => name.toLowerCase().includes(q));
  }, [channels, query]);
  const visible = useMemo(() => new Set(filtered), [filtered]);

  const setSelection = (next: Set<string>) => onChange(channels.filter((c) => next.has(c)));

  function toggle(name: string) {
    const next = new Set(selectedChannels);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSelection(next);
  }

  function toggleShaft(names: string[]) {
    const next = new Set(selectedChannels);
    if (names.every((n) => next.has(n))) names.forEach((n) => next.delete(n));
    else names.forEach((n) => next.add(n));
    setSelection(next);
  }

  return (
    <Stack gap={4}>
      <Text size="xs" fw={500}>
        Channels ({selectedChannels.size}/{channels.length})
      </Text>
      <TextInput
        size="xs"
        aria-label="Search channels"
        placeholder="Search channels"
        value={query}
        onChange={(e) => setQuery(e.currentTarget.value)}
      />
      <Group gap={4}>
        {shafts.map(([shaft, names]) => (
          <Button key={shaft} size="compact-xs" variant="light" onClick={() => toggleShaft(names)}>
            {shaft}
          </Button>
        ))}
      </Group>
      <ScrollArea h={260}>
        <Stack gap={2}>
          {filtered.map((name) => (
            <Checkbox
              key={name}
              size="xs"
              label={name}
              c={aux.has(name) ? "dimmed" : undefined}
              checked={selectedChannels.has(name)}
              onChange={() => toggle(name)}
            />
          ))}
        </Stack>
      </ScrollArea>
      <Text size="xs" c="dimmed">
        Dimmed names are not SEEG contacts (REF, DC, EKG, mark traces). They start unselected because a
        CAR montage would otherwise mix them into every other channel.
      </Text>
      {/* Both act on what the search box is showing -- selecting everything
          while a query is typed is never what a search-then-select means. */}
      <Group gap={4} grow>
        <Button
          size="xs"
          variant="light"
          onClick={() => setSelection(new Set([...selectedChannels, ...filtered]))}
        >
          Select shown
        </Button>
        <Button
          size="xs"
          variant="light"
          onClick={() => setSelection(new Set([...selectedChannels].filter((c) => !visible.has(c))))}
        >
          Clear shown
        </Button>
        <Button size="xs" variant="light" onClick={() => setSelection(new Set(channels.filter((c) => !aux.has(c))))}>
          Contacts only
        </Button>
      </Group>
    </Stack>
  );
}
