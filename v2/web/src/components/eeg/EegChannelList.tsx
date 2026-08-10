import { useState } from "react";
import { Button, Checkbox, Group, ScrollArea, Stack, Text } from "@mantine/core";

interface EegChannelListProps {
  channels: string[];
  excludedChannels: Set<string>;
  onDelete: (channels: string[]) => void;
  onRestore: (channels: string[]) => void;
}

/** Multi-select + delete, mirroring the legacy trace viewers' "Delete
 * channels" list -- removes channels from the working set for this session,
 * not destructive to the uploaded EDF.
 *
 * Excluded channels stay listed rather than disappearing: auxiliary traces are
 * removed automatically when a recording loads (see the LOAD_RECORDING
 * dispatch in IctalPage/InterictalPage), and an automatic decision the user
 * cannot see or undo is worse than the trap it avoids. */
export function EegChannelList({ channels, excludedChannels, onDelete, onRestore }: EegChannelListProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function toggle(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function apply(action: (channels: string[]) => void) {
    action(Array.from(selected));
    setSelected(new Set());
  }

  const remaining = channels.filter((c) => !excludedChannels.has(c));
  const excluded = channels.filter((c) => excludedChannels.has(c));
  const selectedExcludedCount = excluded.filter((c) => selected.has(c)).length;

  return (
    <Stack gap={4}>
      <Text size="xs" fw={500}>
        Channels ({remaining.length}/{channels.length})
      </Text>
      <ScrollArea h={140}>
        <Stack gap={2}>
          {remaining.map((name) => (
            <Checkbox key={name} size="xs" label={name} checked={selected.has(name)} onChange={() => toggle(name)} />
          ))}
        </Stack>
      </ScrollArea>
      {excluded.length > 0 && (
        <>
          <Text size="xs" fw={500} c="dimmed">
            Excluded ({excluded.length})
          </Text>
          <Text size="xs" c="dimmed">
            Names that are not SEEG contacts are excluded automatically: every module re-references to the
            mean of the channels it is given, so a DC or mark trace leaks into all of them.
          </Text>
          <ScrollArea h={80}>
            <Stack gap={2}>
              {excluded.map((name) => (
                <Checkbox
                  key={name}
                  size="xs"
                  label={name}
                  c="dimmed"
                  checked={selected.has(name)}
                  onChange={() => toggle(name)}
                />
              ))}
            </Stack>
          </ScrollArea>
        </>
      )}
      <Group gap={4} grow>
        <Button
          size="xs"
          variant="light"
          color="red"
          disabled={selected.size === selectedExcludedCount}
          onClick={() => apply(onDelete)}
        >
          Delete selected
        </Button>
        <Button size="xs" variant="light" disabled={selectedExcludedCount === 0} onClick={() => apply(onRestore)}>
          Restore selected
        </Button>
      </Group>
    </Stack>
  );
}
