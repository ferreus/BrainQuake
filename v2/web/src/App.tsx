import { useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell, Badge, Button, Group, MantineProvider, Title } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { JobsDrawer } from "./features/jobs/JobsDrawer";
import { SubjectList } from "./features/subjects/SubjectList";
import { SubjectsListPage } from "./routes/SubjectsListPage";
import { SubjectLayoutPage } from "./routes/SubjectLayoutPage";
import { ColorSchemeToggle } from "./components/ColorSchemeToggle";
import brainquakeIcon from "./assets/round_icon_min.png";

const queryClient = new QueryClient();

// ~4 job rows before scrolling (was 260, sized for ~6 -- the panel was
// crowding out the main content for a feature people check occasionally).
const JOBS_FOOTER_HEIGHT = 180;

function Layout() {
  const [jobsCollapsed, setJobsCollapsed] = useState(false);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 260, breakpoint: "sm" }}
      footer={{ height: JOBS_FOOTER_HEIGHT, collapsed: jobsCollapsed }}
      padding={0}
    >
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
            <Button variant="subtle" size="xs" onClick={() => setJobsCollapsed((c) => !c)}>
              {jobsCollapsed ? "Show jobs ▲" : "Hide jobs ▼"}
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
