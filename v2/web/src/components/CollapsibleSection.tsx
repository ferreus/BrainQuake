import { useState, type ReactNode } from "react";
import { Collapse, Group, Paper, Stack, Text, UnstyledButton } from "@mantine/core";

interface CollapsibleSectionProps {
  title: string;
  /** Count or ratio shown next to the title, e.g. "44/128". */
  badge?: string | number;
  defaultOpened?: boolean;
  children: ReactNode;
}

/** Side-panel section with a click-to-collapse header. The app has no Accordion
 * styling to match, so this is the one place the pattern lives. */
export function CollapsibleSection({ title, badge, defaultOpened = true, children }: CollapsibleSectionProps) {
  const [opened, setOpened] = useState(defaultOpened);

  return (
    <Paper withBorder p="xs" radius="sm">
      <Stack gap={opened ? 6 : 0}>
        <UnstyledButton onClick={() => setOpened((o) => !o)} aria-expanded={opened}>
          <Group gap={6} wrap="nowrap">
            <Text size="xs" c="dimmed">{opened ? "▼" : "▶"}</Text>
            <Text size="xs" fw={600}>{title}</Text>
            {badge != null && <Text size="xs" c="dimmed">({badge})</Text>}
          </Group>
        </UnstyledButton>
        <Collapse expanded={opened}>{children}</Collapse>
      </Stack>
    </Paper>
  );
}
