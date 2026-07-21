import { ActionIcon, useComputedColorScheme, useMantineColorScheme } from "@mantine/core";

export function ColorSchemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme("light", { getInitialValueInEffect: true });

  return (
    <ActionIcon
      variant="subtle"
      size="lg"
      aria-label="Toggle dark mode"
      title={computed === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      onClick={() => setColorScheme(computed === "dark" ? "light" : "dark")}
    >
      {computed === "dark" ? "☀️" : "🌙"}
    </ActionIcon>
  );
}
