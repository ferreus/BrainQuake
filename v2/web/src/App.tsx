import { useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell, Badge, Button, Group, MantineProvider, Modal, Stack, TextInput, Title } from "@mantine/core";
import { Notifications, notifications } from "@mantine/notifications";
import { getBaseUrl, setBaseUrl } from "./api/serverConfig";
import { ConnectionIndicator } from "./features/jobs/ConnectionIndicator";
import { JobsDrawer } from "./features/jobs/JobsDrawer";
import { SubjectList } from "./features/subjects/SubjectList";
import { SubjectsListPage } from "./routes/SubjectsListPage";
import { SubjectLayoutPage } from "./routes/SubjectLayoutPage";
import { ColorSchemeToggle } from "./components/ColorSchemeToggle";
import brainquakeIcon from "./assets/round_icon_min.png";

const queryClient = new QueryClient();

function ServerSettingsModal({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const [url, setUrl] = useState(getBaseUrl());

  function handleSave() {
    setBaseUrl(url);
    queryClient.clear();
    notifications.show({ color: "green", title: "Server updated", message: url });
    onClose();
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Server Settings">
      <Stack>
        <TextInput
          label="Server base URL"
          placeholder="http://127.0.0.1:8000"
          value={url}
          onChange={(e) => setUrl(e.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave}>Save</Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function Layout() {
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <AppShell header={{ height: 56 }} navbar={{ width: 260, breakpoint: "sm" }} footer={{ height: 260 }} padding={0}>
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="xs">
            <img src={brainquakeIcon} alt="" width={28} height={28} style={{ borderRadius: "50%" }} />
            <Title order={4}>BrainQuake</Title>
            <Badge variant="light" size="sm">
              v2
            </Badge>
          </Group>
          <Group>
            <ConnectionIndicator />
            <Button variant="subtle" size="xs" onClick={() => setSettingsOpen(true)}>
              Server Settings
            </Button>
            <ColorSchemeToggle />
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar>
        <SubjectList />
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>

      <AppShell.Footer>
        <JobsDrawer />
      </AppShell.Footer>

      <ServerSettingsModal opened={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </AppShell>
  );
}

export default function App() {
  return (
    <MantineProvider defaultColorScheme="auto">
      <Notifications />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Navigate to="/subjects" replace />} />
              <Route path="/subjects" element={<SubjectsListPage />} />
              <Route path="/subjects/:subjectId" element={<SubjectLayoutPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}
