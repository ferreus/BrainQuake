import { Box, Button, Group, Paper, Stack, Text } from "@mantine/core";
import { ApiError } from "../../api/client";

interface EdfLoadErrorPanelProps {
  title: string;
  error: unknown;
  onRetry: () => void;
  onRemove?: () => void;
  removing?: boolean;
}

/** A failed fetch (e.g. the EDF's backing file missing on the server) isn't
 * an emergency the whole panel should shout about -- a neutral, theme-
 * matching surface with a small red status dot carries the "this failed"
 * signal without drowning the actual (often one-line) error message. The
 * "Remove from server" button is the one place color is used deliberately,
 * since it's a destructive action. */
export function EdfLoadErrorPanel({ title, error, onRetry, onRemove, removing }: EdfLoadErrorPanelProps) {
  const message = error instanceof ApiError ? error.message : String(error);

  return (
    <Paper withBorder p="sm" radius="sm">
      <Group gap="xs" align="flex-start" wrap="nowrap">
        <Box
          mt={6}
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            flexShrink: 0,
            background: "var(--mantine-color-red-6)",
          }}
        />
        <Stack gap={4} style={{ flex: 1, minWidth: 0 }}>
          <Text size="sm" fw={500}>
            {title}
          </Text>
          <Text size="xs" c="dimmed">
            {message}
          </Text>
          <Group gap="xs" mt={4}>
            <Button size="xs" variant="default" onClick={onRetry}>
              Retry
            </Button>
            {onRemove && (
              <Button size="xs" variant="subtle" color="red" onClick={onRemove} loading={removing}>
                Remove from server
              </Button>
            )}
          </Group>
        </Stack>
      </Group>
    </Paper>
  );
}
