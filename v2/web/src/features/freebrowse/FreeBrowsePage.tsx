import { Alert } from "@mantine/core";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useLastJob } from "../../api/queries/useJobs";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface FreeBrowsePageProps {
  subjectId: number;
}

/**
 * Embeds FreeBrowse (v2/freebrowse -- a vendored, unmodified git submodule
 * of https://github.com/freesurfer/freebrowse, built and served by nginx
 * under /freebrowse/, see v2/docker/nginx.conf) pointed at this subject's
 * own volumes/surfaces via its own, already-existing `?nvd=<url>` query-param
 * support (frontend/src/hooks/use-file-loading.ts in the submodule -- not
 * something added for this). The .nvd document itself is generated
 * server-side per request from whichever FreeSurfer outputs currently exist
 * on disk (services/freebrowse.py) -- nothing to upload or pick manually.
 */
export function FreeBrowsePage({ subjectId }: FreeBrowsePageProps) {
  // Same derivation as ElectrodesPage.tsx's reconComplete: subject.subject_dir
  // is set at subject *creation*, long before any recon runs, so it can't be
  // used to gate this -- the recon job's own verdict decides, with orig_nii
  // (recon.py's first registered artifact) as the fallback for subjects with
  // no recon job row (e.g. imported subjects).
  const { data: artifacts } = useArtifacts(subjectId);
  const reconJob = useLastJob(subjectId, "recon");
  const reconComplete = reconJob
    ? reconJob.state === "finished"
    : (artifacts ?? []).some((a) => a.kind === "orig_nii");

  if (!reconComplete) {
    const running = reconJob && !TERMINAL_JOB_STATES.has(reconJob.state);
    return (
      <Alert color="gray" variant="light" mt="md">
        {running
          ? "Waiting for brain reconstruction to finish before FreeBrowse has anything to show."
          : "Run brain reconstruction first -- FreeBrowse needs its output (orig.mgz, surfaces)."}
      </Alert>
    );
  }

  const nvdUrl = `/api/subjects/${subjectId}/freebrowse.nvd`;
  return (
    <iframe
      key={subjectId}
      title="FreeBrowse"
      src={`/freebrowse/index.html?nvd=${encodeURIComponent(nvdUrl)}`}
      style={{ width: "100%", flex: 1, minHeight: 0, border: "none" }}
    />
  );
}
