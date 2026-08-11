import { useEffect, useRef, useState } from "react";
import { Button, FileButton, Group, Loader, Paper, Progress, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, uploadFileWithProgress } from "../../api/client";
import { useArtifacts, useDeleteElectrodeContacts, useRegisterCt } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { useLastJob } from "../../api/queries/useJobs";
import { useRebuildSurface, useSurfaceMesh } from "../../api/queries/useSurfaceMesh";
import { TERMINAL_JOB_STATES } from "../../api/types";
import type { Artifact, Job } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { ClusterCentroids } from "../../components/three/ClusterCentroids";
import { ElectrodeContacts } from "../../components/three/ElectrodeContacts";
import { SceneCanvas } from "../../components/three/SceneCanvas";
import { SlicerContactsPreview } from "../../components/three/SlicerContactsPreview";
import { ContactAnatomyPanel } from "./ContactAnatomyPanel";
import { DetectForm } from "./DetectForm";
import { ImportSlicerForm } from "./ImportSlicerForm";
import { LabelReviewPanel } from "./LabelReviewPanel";
import { SegmentForm } from "./SegmentForm";
import { SlicerImportReviewPanel } from "./SlicerImportReviewPanel";

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
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
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

  // Every upload inserts a fresh raw_ct artifact row (see routers/subjects.py),
  // which is what ctStale below compares against the last registration's
  // timestamp -- this is the only way to get a newer CT onto the server once a
  // subject already exists, since NewSubjectDialog only uploads one at creation.
  async function handleUploadCt(file: File | null) {
    if (!file) return;
    setUploadProgress(0);
    try {
      await uploadFileWithProgress(`/subjects/${subjectId}/upload`, file, "ct", setUploadProgress).promise;
      queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to upload CT",
        message: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setUploadProgress(null);
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
    status = "Upload a CT scan for this subject first";
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
      <Group justify="flex-end" mt="xs">
        <FileButton onChange={handleUploadCt} accept=".nii.gz,.nii,application/gzip">
          {(props) => (
            <Button size="xs" variant="default" loading={uploadProgress != null} {...props}>
              {hasRawCt ? "Replace CT" : "Upload CT"}
            </Button>
          )}
        </FileButton>
      </Group>
      {uploadProgress != null && <Progress value={uploadProgress * 100} size="xs" mt={4} animated />}
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

interface DeleteContactsButtonProps {
  subjectId: number;
  disabled: boolean;
}

/** Clears clusters (detect()'s review data) and contacts (segment()'s or an
 * import's), from the bottom of the sidebar -- the one way to throw out a bad
 * hough3dlines/GMM run or a Slicer import and start the electrodes tab over. */
function DeleteContactsButton({ subjectId, disabled }: DeleteContactsButtonProps) {
  const deleteContacts = useDeleteElectrodeContacts(subjectId);

  async function handleDelete() {
    if (!confirm("Delete all clusters and contacts for this subject? This cannot be undone.")) return;
    try {
      await deleteContacts.mutateAsync();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to delete contacts",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  return (
    // flexShrink: 0 -- as a direct child of the sidebar Stack (a scrollable flex
    // column), a bare <button> is uniquely prone to being crushed by the shrink
    // algorithm down to ~1px: form controls resolve their flex auto min-height
    // to 0 instead of content size the way a <div> (e.g. the Paper cards above
    // it) does, so without this it can render present-but-unclickable at the
    // bottom of the scroll area.
    <Button
      color="red"
      loading={deleteContacts.isPending}
      disabled={disabled}
      onClick={handleDelete}
      style={{ flexShrink: 0 }}
    >
      Delete Contacts
    </Button>
  );
}

interface ElectrodesPageProps {
  subjectId: number;
  /** Mount the WebGL canvas only while this view is visible -- hidden WebGL
   * contexts get lost by the browser (esp. alongside FreeBrowse's own context)
   * and come back black. */
  active: boolean;
}

export function ElectrodesPage({ subjectId, active }: ElectrodesPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const queryClient = useQueryClient();
  const kinds = new Set((artifacts ?? []).map((a) => a.kind));
  const hasRawCt = kinds.has("raw_ct");
  const detected = kinds.has("labels_npy");
  const segmented = kinds.has("chnXyzDict");
  const hasPendingSlicerPreview = kinds.has("slicer_contacts_preview");
  const [excludedClusters, setExcludedClusters] = useState<Set<number>>(new Set());
  // Contact name (e.g. "K'7"), shared by the 3D view and the anatomy table so
  // selecting in one highlights the other. Names, not indices -- the two get
  // their contacts from different endpoints with different orderings.
  const [selectedContact, setSelectedContact] = useState<string | null>(null);

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
    <Group align="stretch" wrap="nowrap" gap="md" mt="md" style={{ flex: 1, minHeight: 0 }}>
      <div style={{ flex: 1, height: "100%", position: "relative" }}>
        {active && (
          <SceneCanvas>
            <BrainMesh subjectId={subjectId} />
            {hasPendingSlicerPreview ? (
              <SlicerContactsPreview subjectId={subjectId} />
            ) : segmented ? (
              <ElectrodeContacts subjectId={subjectId} selected={selectedContact} onSelect={setSelectedContact} />
            ) : (
              detected && <ClusterCentroids subjectId={subjectId} excluded={excludedClusters} />
            )}
          </SceneCanvas>
        )}
        {surfaceMissing && <SurfaceRebuildBanner subjectId={subjectId} activeRecon={activeRecon} />}
      </div>
      <Stack w={360} h="100%" gap="md" style={{ overflowY: "auto" }}>
        <ImportSlicerForm subjectId={subjectId} disabled={!reconComplete} hasPendingPreview={hasPendingSlicerPreview} />
        {hasPendingSlicerPreview && <SlicerImportReviewPanel subjectId={subjectId} />}
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
        {segmented && (
          <ContactAnatomyPanel subjectId={subjectId} selected={selectedContact} onSelect={setSelectedContact} />
        )}
        <DeleteContactsButton subjectId={subjectId} disabled={!detected && !segmented} />
      </Stack>
    </Group>
  );
}
