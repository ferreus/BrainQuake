import { Stack, Text, Title } from "@mantine/core";

/** Shown at `/subjects` (no subject selected yet). */
export function SubjectsListPage() {
  return (
    <Stack align="center" justify="center" h="100%" gap={4}>
      <Title order={3} c="dimmed">
        No patient selected
      </Title>
      <Text c="dimmed">Choose a patient from the list on the left, or create a new one.</Text>
    </Stack>
  );
}
