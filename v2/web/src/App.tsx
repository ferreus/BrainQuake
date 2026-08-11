import { useRef, useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell, Badge, Group, MantineProvider, Title } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { Canvas } from "@react-three/fiber";
import { View } from "@react-three/drei";
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
// Wide enough for the header row's "Import" + "New Subject" buttons.
const SUBJECTS_PANEL_WIDTH = 300;

function Layout() {
  const [jobsCollapsed, setJobsCollapsed] = useState(false);
  const [subjectsOpen, setSubjectsOpen] = useState(true);
  const mainRef = useRef<HTMLElement>(null);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: ACTIVITY_BAR_WIDTH + (subjectsOpen ? SUBJECTS_PANEL_WIDTH : 0), breakpoint: "sm" }}
      footer={{ height: JOBS_FOOTER_HEIGHT, collapsed: jobsCollapsed }}
      padding={0}
      // No width animation: each animation frame resizes every mounted view
      // (EEG canvases, iframe, r3f), which janks the panel toggle badly.
      transitionDuration={0}
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

      <AppShell.Main ref={mainRef}>
        <Outlet />
      </AppShell.Main>

      {/* The app's only WebGL context, mounted once for the session. Every 3D
          page draws through a <SceneView>, which portals into this canvas and
          is scissored to its own layout box -- so switching subject or view
          rebuilds scene contents but never the context. Deliberately outside
          the routes: SubjectLayoutPage early-returns a loader while the next
          subject loads, which would unmount a canvas rendered there.
          z-index 1 keeps it above the page background but below Mantine's
          AppShell chrome (100) and its modal/menu portals (200+). */}
      <Canvas
        eventSource={mainRef as React.RefObject<HTMLElement>}
        gl={{ alpha: true }}
        style={{ position: "fixed", inset: 0, pointerEvents: "none", zIndex: 1 }}
      >
        <View.Port />
      </Canvas>

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
