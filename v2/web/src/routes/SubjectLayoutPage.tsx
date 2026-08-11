import { useState } from "react";
import type { CSSProperties } from "react";
import { useParams } from "react-router-dom";
import { Alert, Loader, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { getSubject } from "../api/endpoints";
import { DEFAULT_VIEW, SUBJECT_VIEWS, type SubjectView } from "../components/subjectViews";
import { ElectrodesPage } from "../features/electrodes/ElectrodesPage";
import { FreeBrowsePage } from "../features/freebrowse/FreeBrowsePage";
import { IctalPage } from "../features/ictal/IctalPage";
import { InterictalPage } from "../features/interictal/InterictalPage";
import { SozPage } from "../features/soz/SozPage";

export function SubjectLayoutPage() {
  const { subjectId, view } = useParams();
  const id = Number(subjectId);
  const activeView: SubjectView = SUBJECT_VIEWS.some((v) => v.value === view)
    ? (view as SubjectView)
    : DEFAULT_VIEW;

  const { data: subject, isLoading, isError } = useQuery({
    queryKey: ["subject", id],
    queryFn: () => getSubject(id),
    enabled: Number.isFinite(id),
  });

  // Views mount on first visit and stay mounted after, reset per subject.
  // Mounting all five at once froze the UI for 10+ seconds on subject switch:
  // the FreeBrowse iframe alone gunzips three MGZ volumes on the shared main
  // thread, plus two EDF viewers fetching/drawing and two 3D pages parsing
  // surfaces -- all for views not being looked at.
  const [visited, setVisited] = useState<{ id: number; views: Set<SubjectView> }>(() => ({
    id,
    views: new Set([activeView]),
  }));
  if (visited.id !== id) {
    setVisited({ id, views: new Set([activeView]) });
  } else if (!visited.views.has(activeView)) {
    setVisited({ id, views: new Set(visited.views).add(activeView) });
  }
  const isVisited = (v: SubjectView) => visited.id === id && visited.views.has(v);

  if (isLoading) {
    return (
      <Stack align="center" justify="center" h="100%">
        <Loader />
      </Stack>
    );
  }

  if (isError || !subject) {
    return (
      <Stack align="center" justify="center" h="100%">
        <Text c="red">Subject not found.</Text>
      </Stack>
    );
  }

  // All views stay mounted (display-toggled) so their local state -- EEG pan
  // position, form inputs -- survives view switches, matching the old
  // keepMounted Tabs behavior. WebGL canvases are the exception: see the
  // `active` prop on ElectrodesPage/SozPage.
  const viewStyle = (v: SubjectView): CSSProperties => ({
    display: activeView === v ? "flex" : "none",
    flexDirection: "column",
    height: "100%",
    minHeight: 0,
  });

  return (
    <Stack h="100%" p="md" gap="sm" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <Stack gap={2}>
        <Title order={3}>{subject.name}</Title>
        <Text size="sm" c="dimmed">
          Reconstruction type: {subject.recon_type ?? "not set"}
          {subject.subject_dir ? ` — ${subject.subject_dir}` : " — reconstruction not yet run"}
        </Text>
      </Stack>

      {/* key={id} on every per-subject child: switching subjects in the sidebar
          re-renders this route with a new :subjectId but does NOT remount it,
          so without a key the pages keep the previous subject's local state --
          most visibly the job ids the step forms poll on. */}
      <div style={{ flex: 1, minHeight: 0 }}>
        <div style={viewStyle("electrodes")}>
          {isVisited("electrodes") &&
            (subject.subject_dir ? (
              <ElectrodesPage key={id} subjectId={id} active={activeView === "electrodes"} />
            ) : (
              <Alert color="gray" variant="light" mt="md">
                Run reconstruction first (see the subject's upload step) before working on electrodes.
              </Alert>
            ))}
        </div>
        <div style={viewStyle("ictal")}>
          {isVisited("ictal") && <IctalPage key={id} subjectId={id} />}
        </div>
        <div style={viewStyle("interictal")}>
          {isVisited("interictal") && <InterictalPage key={id} subjectId={id} />}
        </div>
        <div style={viewStyle("soz")}>
          {isVisited("soz") && <SozPage key={id} subjectId={id} active={activeView === "soz"} />}
        </div>
        <div style={viewStyle("freebrowse")}>
          {isVisited("freebrowse") && <FreeBrowsePage key={id} subjectId={id} />}
        </div>
      </div>
    </Stack>
  );
}
