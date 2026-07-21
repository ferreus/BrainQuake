import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJob } from "../endpoints";
import { TERMINAL_JOB_STATES } from "../types";
import type { Job } from "../types";

/**
 * Polls a single job until it reaches a terminal state, mirroring
 * v2/client/api_client.py's wait_for_job(). `onTerminal` fires exactly once
 * per job id when it first reaches finished/failed/cancelled -- later phases
 * use this to invalidate whatever resource query that job_type affects (e.g.
 * a finished `recon` job invalidating the cached surface mesh) instead of
 * requiring a manual reload.
 */
export function useJobPolling(jobId: number | undefined, onTerminal?: (job: Job) => void) {
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId!),
    enabled: jobId != null,
    refetchInterval: (q) => {
      const state = q.state.data?.state;
      return state && TERMINAL_JOB_STATES.has(state) ? false : 1500;
    },
  });

  const firedForJobId = useRef<number | null>(null);
  useEffect(() => {
    const job = query.data;
    if (job && TERMINAL_JOB_STATES.has(job.state) && firedForJobId.current !== job.id) {
      firedForJobId.current = job.id;
      onTerminal?.(job);
    }
  }, [query.data, onTerminal]);

  return query;
}
