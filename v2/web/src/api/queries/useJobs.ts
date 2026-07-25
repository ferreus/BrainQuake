import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelJob, deleteJob, getJobLog, listJobs, retryJob } from "../endpoints";
import type { Job } from "../types";

/** Jobs drawer list -- refetches on an interval while mounted so progress
 * bars/state badges update without the user doing anything. Cheaper than a
 * websocket for this app's scale (single-user, local/trust network). */
export function useJobs(params?: { subjectId?: number }) {
  return useQuery({
    queryKey: ["jobs", params?.subjectId ?? "all"],
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

export function useJobLog(jobId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["job-log", jobId],
    queryFn: () => getJobLog(jobId!),
    enabled: enabled && jobId != null,
    refetchInterval: enabled ? 2000 : false,
  });
}

export function useCancelJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => cancelJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useDeleteJob() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) => deleteJob(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

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
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
