import { useEffect, useMemo, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelJob, deleteJob, getJobLog, listJobs, retryJob } from "../endpoints";
import { qk } from "../queryKeys";
import { makeMutation } from "./factories";
import { TERMINAL_JOB_STATES } from "../types";
import type { Job } from "../types";

/** Jobs drawer list -- refetches on an interval while mounted so progress
 * bars/state badges update without the user doing anything. Cheaper than a
 * websocket for this app's scale (single-user, local/trust network). */
export function useJobs(params?: { subjectId?: number }) {
  return useQuery({
    queryKey: qk.jobsList(params?.subjectId),
    queryFn: () => listJobs(params?.subjectId != null ? { subjectId: params.subjectId } : undefined),
    refetchInterval: 3000,
  });
}

/** Most recent job of `jobType` for a subject, for prefilling a form with
 * whatever params were last submitted -- e.g. detect/segment settings, which
 * otherwise reset to hardcoded defaults on every page reload since they're
 * only ever held in local component state. */
export function useLastJob(subjectId: number | undefined, jobType: string): Job | undefined {
  const { data: jobs } = useJobs({ subjectId });
  return useMemo(() => {
    const matches = (jobs ?? []).filter((j) => j.job_type === jobType);
    return matches.reduce<Job | undefined>((latest, j) => (!latest || j.id > latest.id ? j : latest), undefined);
  }, [jobs, jobType]);
}

/**
 * Fires `onFinished` once when a job that was previously seen in flight
 * reaches `finished`. Unlike useJobPolling this watches a job the component
 * did not start -- e.g. a recon queued from the New Patient dialog finishing
 * while the Electrodes page happens to be open, whose output that page's
 * `staleTime: Infinity` surface/artifact queries need to pick up.
 *
 * Keyed on having actually seen the job running so a plain revisit (job
 * already long finished) doesn't needlessly refetch what's already cached.
 */
export function useOnJobFinished(job: Job | undefined, onFinished: () => void) {
  const active = job != null && !TERMINAL_JOB_STATES.has(job.state);
  const finished = job?.state === "finished";
  const sawActive = useRef(false);
  const callback = useRef(onFinished);
  callback.current = onFinished;

  useEffect(() => {
    if (active) {
      sawActive.current = true;
    } else if (finished && sawActive.current) {
      sawActive.current = false;
      callback.current();
    }
  }, [active, finished]);
}

export function useJobLog(jobId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: qk.jobLog(jobId),
    queryFn: () => getJobLog(jobId!),
    enabled: enabled && jobId != null,
    refetchInterval: enabled ? 2000 : false,
  });
}

export const useCancelJob = makeMutation((jobId: number) => cancelJob(jobId), [qk.jobs()]);

export const useDeleteJob = makeMutation((jobId: number) => deleteJob(jobId), [qk.jobs()]);

export function useRetryJob() {
  const queryClient = useQueryClient();
  return useMutation({
    // Once the replacement job exists, drop the old failed/cancelled row so
    // repeated Retry clicks can't pile up duplicates. Best-effort: the retry
    // already succeeded, so a failed cleanup shouldn't surface as an error.
    mutationFn: async (job: Job) => {
      const newJob = await retryJob(job);
      await deleteJob(job.id).catch(() => {});
      return newJob;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: qk.jobs() });
    },
  });
}
