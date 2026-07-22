import { useState } from "react";
import { Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useArtifacts, useRegisterCt } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { useRebuildSurface, useSurfaceMesh } from "../../api/queries/useSurfaceMesh";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { ClusterCentroids } from "../../components/three/ClusterCentroids";
import { ElectrodeContacts } from "../../components/three/ElectrodeContacts";
import { SceneCanvas } from "../../components/three/SceneCanvas";
import { DetectForm } from "./DetectForm";
import { LabelReviewPanel } from "./LabelReviewPanel";
import { SegmentForm } from "./SegmentForm";

interface RegisterCtStepProps {
  subjectId: number;
  hasRawCt: boolean;
  ctRegistered: boolean;
}

function RegisterCtStep({ subjectId, hasRawCt, ctRegistered }: RegisterCtStepProps) {
  const [jobId, setJobId] = useState<number | undefined>();
  const registerCt = useRegisterCt(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({
        color: "red",
        title: "CT registration failed",
        message: finishedJob.progress_message ?? "",
      });
    }
  });

  async function handleRegister() {
    try {
      const j = await registerCt.mutateAsync();
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start CT registration",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        0. Register CT
      </Title>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {ctRegistered
            ? "CT registered to MRI space"
            : hasRawCt
              ? "CT uploaded, not yet registered"
              : "Upload a CT scan for this patient first"}
        </Text>
        <Button size="xs" loading={running} disabled={!hasRawCt} onClick={handleRegister}>
          {ctRegistered ? "Re-register" : "Register"}
        </Button>
      </Group>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}

/** Shown over the 3D pane when neither hemisphere has a cached mesh yet --
 * see useRebuildSurface. Without this, a subject in that state just renders
 * an empty canvas with no indication why or how to fix it. */
function SurfaceRebuildBanner({ subjectId }: { subjectId: number }) {
  const [jobId, setJobId] = useState<number | undefined>();
  const rebuild = useRebuildSurface(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    if (finishedJob.state === "finished") {
      queryClient.invalidateQueries({ queryKey: ["surface", subjectId] });
    } else if (finishedJob.state === "failed") {
      notifications.show({
        color: "red",
        title: "Surface export failed",
        message: finishedJob.progress_message ?? "",
      });
    }
  });

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : rebuild.isPending;

  async function handleGenerate() {
    try {
      const j = await rebuild.mutateAsync();
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start surface export",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        pointerEvents: "none",
      }}
    >
      <Stack align="center" gap="xs" style={{ pointerEvents: "auto" }}>
        <Text size="sm" c="dimmed">
          No cached brain surface for this subject yet.
        </Text>
        <Button size="xs" loading={running} onClick={handleGenerate}>
          Generate brain surface
        </Button>
      </Stack>
    </div>
  );
}

interface ElectrodesPageProps {
  subjectId: number;
}

export function ElectrodesPage({ subjectId }: ElectrodesPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const kinds = new Set((artifacts ?? []).map((a) => a.kind));
  const hasRawCt = kinds.has("raw_ct");
  const ctRegistered = kinds.has("ct_reg_nii");
  const detected = kinds.has("labels_npy");
  const segmented = kinds.has("chnXyzDict");
  const [excludedClusters, setExcludedClusters] = useState<Set<number>>(new Set());

  const lhSurface = useSurfaceMesh(subjectId, "lh");
  const rhSurface = useSurfaceMesh(subjectId, "rh");
  const surfaceMissing = lhSurface.isError && rhSurface.isError;

  return (
    // Fixed viewport-relative height (not h="100%") on purpose: none of this
    // page's ancestors (AppShell.Main, Tabs.Panel, ...) clip overflow, so a
    // percentage height here just reflects however tall the content wants to
    // be. With align="stretch" + h="100%", a long sidebar (many clusters in
    // "2. Review Clusters", the segment form, etc.) used to stretch this
    // whole row -- canvas included -- taller than the browser window, which
    // pushed the actually-rendered 3D scene below the fold: it wasn't
    // missing, just scrolled out of view. Pinning to 70vh and letting the
    // sidebar scroll internally keeps the 3D view on-screen regardless of
    // how much sidebar content there is.
    <Group align="stretch" wrap="nowrap" gap="md" mt="md" h="70vh">
      <div style={{ flex: 1, minHeight: 520, height: "100%", position: "relative" }}>
        <SceneCanvas>
          <BrainMesh subjectId={subjectId} />
          {segmented ? (
            <ElectrodeContacts subjectId={subjectId} />
          ) : (
            detected && <ClusterCentroids subjectId={subjectId} excluded={excludedClusters} />
          )}
        </SceneCanvas>
        {surfaceMissing && <SurfaceRebuildBanner subjectId={subjectId} />}
      </div>
      <Stack w={360} h="100%" gap="md" style={{ overflowY: "auto" }}>
        <RegisterCtStep subjectId={subjectId} hasRawCt={hasRawCt} ctRegistered={ctRegistered} />
        <DetectForm subjectId={subjectId} disabled={!ctRegistered} detected={detected} />
        {detected && (
          <LabelReviewPanel subjectId={subjectId} excluded={excludedClusters} onExcludedChange={setExcludedClusters} />
        )}
        <SegmentForm subjectId={subjectId} disabled={!detected} segmented={segmented} />
      </Stack>
    </Group>
  );
}
