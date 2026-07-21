import { useState } from "react";
import { Button, Checkbox, ScrollArea, Stack, Text } from "@mantine/core";

interface EegChannelListProps {
  channels: string[];
  excludedChannels: Set<string>;
  onDelete: (channels: string[]) => void;
}

/** Multi-select + delete, mirroring the legacy trace viewers' "Delete
 * channels" list -- removes channels from the working set for this session,
 * not destructive to the uploaded EDF. */
export function EegChannelList({ channels, excludedChannels, onDelete }: EegChannelListProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function toggle(name: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function handleDelete() {
    onDelete(Array.from(selected));
    setSelected(new Set());
  }

  const remaining = channels.filter((c) => !excludedChannels.has(c));

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
      <Button size="xs" variant="light" color="red" disabled={selected.size === 0} onClick={handleDelete}>
        Delete selected
      </Button>
    </Stack>
  );
}
