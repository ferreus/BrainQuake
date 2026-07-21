import { useQuery } from "@tanstack/react-query";
import { Group, Text, Tooltip } from "@mantine/core";
import { getBaseUrl } from "../../api/serverConfig";

async function pingServer(): Promise<boolean> {
  const res = await fetch(getBaseUrl(), { method: "GET" });
  return res.ok;
}

export function ConnectionIndicator() {
  const baseUrl = getBaseUrl();
  const { data: connected } = useQuery({
    queryKey: ["server-health", baseUrl],
    queryFn: pingServer,
    refetchInterval: 5000,
    retry: false,
  });

  const color = connected ? "var(--mantine-color-green-6)" : "var(--mantine-color-red-6)";
  const label = connected ? "Connected" : "Disconnected";

  return (
    <Tooltip label={`${label}: ${baseUrl}`}>
      <Group gap={6} wrap="nowrap">
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            backgroundColor: color,
            display: "inline-block",
          }}
        />
        <Text size="sm" c="dimmed">
          {label}
        </Text>
      </Group>
    </Tooltip>
  );
}
