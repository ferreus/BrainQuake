import { useEffect, useRef, useState } from "react";
import { Button, Group, Loader, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useArtifacts, useRegisterCt } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { useLastJob } from "../../api/queries/useJobs";
import { useRebuildSurface, useSurfaceMesh } from "../../api/queries/useSurfaceMesh";
import { TERMINAL_JOB_STATES } from "../../api/types";
import type { Artifact, Job } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { ClusterCentroids } from "../../components/three/ClusterCentroids";
import { ElectrodeContacts } from "../../components/three/ElectrodeContacts";
import { SceneCanvas } from "../../components/three/SceneCanvas";
import { DetectForm } from "./DetectForm";
import { LabelReviewPanel } from "./LabelReviewPanel";
import { SegmentForm } from "./SegmentForm";

/** created_at of the most recent artifact of `kind`, as an epoch ms, or null. */
function newestArtifactTime(artifacts: Artifact[] | undefined, kind: string): number | null {
  return (artifacts ?? [])
    .filter((a) => a.kind === kind)
    .reduce<number | null>((newest, a) => {
      const t = Date.parse(a.created_at);
      return Number.isNaN(t) ? newest : newest == null || t > newest ? t : newest;
    }, null);
}

interface RegisterCtStepProps {
  subjectId: number;
  hasRawCt: boolean;
  /** A registration ran all the way through and is current. */
  ctRegistered: boolean;
  /** A registration exists but a CT was uploaded after it, so it's outdated. */
  ctStale: boolean;
  reconComplete: boolean;
  activeRecon: Job | null;
  /** Newest ct_register job on the server, so a reload mid-registration still
   * shows it as in flight instead of offering a duplicate-queuing button. */
  lastRegisterJob: Job | undefined;
}

function RegisterCtStep({
  subjectId,
  hasRawCt,
  ctRegistered,
  ctStale,
  reconComplete,
  activeRecon,
  lastRegisterJob,
}: RegisterCtStepProps) {
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

  const currentJob = job ?? lastRegisterJob;
  const running = currentJob ? !TERMINAL_JOB_STATES.has(currentJob.state) : false;

  // flirt registers against mri/orig.nii.gz, which only exists once recon has
  // produced orig.mgz -- offering the button any earlier just queues a job that
  // dies on FileNotFoundError. Once a registration is current there is nothing
  // left to do here either; the button comes back only if a newer CT is
  // uploaded, or the registration failed.
  const showButton = reconComplete && (!ctRegistered || ctStale);

  let status: string;
  if (!reconComplete) {
    status = activeRecon
      ? "Waiting for brain reconstruction to finish"
      : "Run brain reconstruction first — CT registration needs its output";
  } else if (ctStale) {
    status = "A newer CT was uploaded after the last registration";
  } else if (ctRegistered) {
    status = "CT registered to MRI space";
  } else if (lastRegisterJob && (lastRegisterJob.state === "failed" || lastRegisterJob.state === "cancelled")) {
    status = `Last registration ${lastRegisterJob.state} — see its job log`;
  } else if (hasRawCt) {
    status = "CT uploaded, not yet registered";
  } else {
    status = "Upload a CT scan for this patient first";
  }

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        0. Register CT
      </Title>
      <Group justify="space-between" wrap="nowrap">
        <Text size="xs" c="dimmed">
          {status}
        </Text>
        {showButton && (
          <Button size="xs" loading={running} disabled={!hasRawCt} onClick={handleRegister}>
            {ctStale ? "Re-register" : "Register"}
          </Button>
        )}
      </Group>
      {currentJob?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {currentJob.progress_message}
        </Text>
      )}
    </Paper>
  );
}

/** Shown over the 3D pane when neither hemisphere has a cached mesh yet --
 * see useRebuildSurface. Without this, a subject in that state just renders
 * an empty canvas with no indication why or how to fix it. */
function SurfaceRebuildBanner({ subjectId, activeRecon }: { subjectId: number; activeRecon: Job | null }) {
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
        {activeRecon ? (
          <>
            <Loader size="sm" />
            <Text size="sm" c="dimmed">
              Reconstruction {activeRecon.state}
              {activeRecon.state === "running" ? ` — ${activeRecon.progress_pct}%` : ""}. The brain surface appears
              here when it finishes.
            </Text>
            {activeRecon.progress_message && (
              <Text size="xs" c="dimmed">
                {activeRecon.progress_message}
              </Text>
            )}
          </>
        ) : (
          <>
            <Text size="sm" c="dimmed">
              No cached brain surface for this subject yet.
            </Text>
            <Button size="xs" loading={running} onClick={handleGenerate}>
              Generate brain surface
            </Button>
          </>
        )}
      </Stack>
    </div>
  );
}

interface ElectrodesPageProps {
  subjectId: number;
}

export function ElectrodesPage({ subjectId }: ElectrodesPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const queryClient = useQueryClient();
  const kinds = new Set((artifacts ?? []).map((a) => a.kind));
  const hasRawCt = kinds.has("raw_ct");
  const detected = kinds.has("labels_npy");
  const segmented = kinds.has("chnXyzDict");
  const [excludedClusters, setExcludedClusters] = useState<Set<number>>(new Set());

  // ct_register registers ct_reg_nii midway through, so that artifact alone
  // does not mean the pipeline finished (the masking and legacy-copy steps that
  // elec_detect actually reads from come after it). Trust the job's own verdict
  // when there is a job row, and fall back to its last artifact for subjects
  // whose job rows were deleted from the Jobs panel or came in via import.
  const lastRegisterJob = useLastJob(subjectId, "ct_register");
  const ctRegArtifactTime = newestArtifactTime(artifacts, "ct_intracranial_nii");
  const ctRegistered = lastRegisterJob
    ? lastRegisterJob.state === "finished"
    : ctRegArtifactTime != null;

  // Every CT upload inserts a fresh raw_ct row (uploads are never deduped, see
  // routers/subjects.py), so a raw_ct newer than the registration means what is
  // on disk was registered from a superseded CT.
  const newestCt = newestArtifactTime(artifacts, "raw_ct");
  const registeredAt = lastRegisterJob?.finished_at
    ? Date.parse(lastRegisterJob.finished_at)
    : ctRegArtifactTime;
  const ctStale = ctRegistered && registeredAt != null && newestCt != null && newestCt > registeredAt;

  // Same shape as above: the recon job's verdict decides, with orig_nii (what
  // flirt registers against) as the fallback for subjects with no recon job row.
  // subject.subject_dir is deliberately not used -- it is filled in at subject
  // creation, long before any recon runs.
  const reconJob = useLastJob(subjectId, "recon");
  const activeRecon = reconJob && !TERMINAL_JOB_STATES.has(reconJob.state) ? reconJob : null;
  const reconComplete = reconJob ? reconJob.state === "finished" : kinds.has("orig_nii");

  const lhSurface = useSurfaceMesh(subjectId, "lh");
  const rhSurface = useSurfaceMesh(subjectId, "rh");
  const surfaceMissing = lhSurface.isError && rhSurface.isError;

  // A recon that completes while this page is open has just written the lh/rh
  // mesh cache and the mri/* artifacts the gating above reads, but nothing
  // refetches either on its own -- useSurfaceMesh is staleTime: Infinity +
  // retry: false, so its 404 from during the recon would stick until a reload.
  // Keyed on having seen the recon in flight so a plain revisit doesn't
  // needlessly re-download a mesh that is already cached and valid.
  const sawActiveRecon = useRef(false);
  useEffect(() => {
    if (activeRecon) {
      sawActiveRecon.current = true;
    } else if (reconComplete && sawActiveRecon.current) {
      sawActiveRecon.current = false;
      queryClient.invalidateQueries({ queryKey: ["surface", subjectId] });
      queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    }
  }, [activeRecon, reconComplete, queryClient, subjectId]);

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
        {surfaceMissing && <SurfaceRebuildBanner subjectId={subjectId} activeRecon={activeRecon} />}
      </div>
      <Stack w={360} h="100%" gap="md" style={{ overflowY: "auto" }}>
        <RegisterCtStep
          subjectId={subjectId}
          hasRawCt={hasRawCt}
          ctRegistered={ctRegistered}
          ctStale={ctStale}
          reconComplete={reconComplete}
          activeRecon={activeRecon}
          lastRegisterJob={lastRegisterJob}
        />
        <DetectForm subjectId={subjectId} disabled={!ctRegistered} detected={detected} />
        {detected && (
          <LabelReviewPanel subjectId={subjectId} excluded={excludedClusters} onExcludedChange={setExcludedClusters} />
        )}
        <SegmentForm subjectId={subjectId} disabled={!detected} segmented={segmented} />
      </Stack>
    </Group>
  );
}
