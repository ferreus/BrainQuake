import { useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell, Badge, Group, MantineProvider, Title } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { ActivityBar } from "./components/ActivityBar";
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

const ACTIVITY_BAR_WIDTH = 48;
const SUBJECTS_PANEL_WIDTH = 260;

function Layout() {
  const [jobsCollapsed, setJobsCollapsed] = useState(false);
  const [subjectsOpen, setSubjectsOpen] = useState(true);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: ACTIVITY_BAR_WIDTH + (subjectsOpen ? SUBJECTS_PANEL_WIDTH : 0), breakpoint: "sm" }}
      footer={{ height: JOBS_FOOTER_HEIGHT, collapsed: jobsCollapsed }}
      padding={0}
      // Definite height + flex on <main> so every page below can use h="100%"
      // instead of the old 70vh workarounds (Mantine's default is only a
      // min-height, which percentage heights can't resolve against).
      styles={{
        main: { height: "100dvh", overflow: "hidden", display: "flex", flexDirection: "column" },
      }}
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
          <ColorSchemeToggle />
        </Group>
      </AppShell.Header>

      <AppShell.Navbar>
        <Group h="100%" gap={0} wrap="nowrap" align="stretch">
          <ActivityBar
            subjectsOpen={subjectsOpen}
            onToggleSubjects={() => setSubjectsOpen((o) => !o)}
            jobsOpen={!jobsCollapsed}
            onToggleJobs={() => setJobsCollapsed((c) => !c)}
          />
          {subjectsOpen && (
            <div style={{ flex: 1, minWidth: 0 }}>
              <SubjectList />
            </div>
          )}
        </Group>
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
              <Route path="/subjects/:subjectId/:view?" element={<SubjectLayoutPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </MantineProvider>
  );
}
