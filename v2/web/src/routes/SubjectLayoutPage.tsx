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
          {subject.subject_dir ? (
            <ElectrodesPage key={id} subjectId={id} active={activeView === "electrodes"} />
          ) : (
            <Alert color="gray" variant="light" mt="md">
              Run reconstruction first (see the subject's upload step) before working on electrodes.
            </Alert>
          )}
        </div>
        <div style={viewStyle("ictal")}>
          <IctalPage key={id} subjectId={id} />
        </div>
        <div style={viewStyle("interictal")}>
          <InterictalPage key={id} subjectId={id} />
        </div>
        <div style={viewStyle("soz")}>
          <SozPage key={id} subjectId={id} active={activeView === "soz"} />
        </div>
        <div style={viewStyle("freebrowse")}>
          <FreeBrowsePage key={id} subjectId={id} />
        </div>
      </div>
    </Stack>
  );
}
