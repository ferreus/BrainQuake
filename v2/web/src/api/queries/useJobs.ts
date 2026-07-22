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
