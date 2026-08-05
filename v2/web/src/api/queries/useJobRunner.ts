import { useCallback, useRef, useState } from "react";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import { showApiError } from "../../lib/notify";
import { useJobPolling } from "./useJobPolling";
import { TERMINAL_JOB_STATES } from "../types";
import type { Job } from "../types";

interface JobRunnerOptions<TArgs> {
  /** Queues the job server-side and resolves with the created row. */
  start: (args: TArgs) => Promise<Job>;
  /** Toast title when the job itself ends in `failed`. */
  failTitle: string;
  /** Toast title when queueing the job never got off the ground. */
  startFailTitle: string;
  /** Invalidated once the job reaches any terminal state. */
  invalidate?: readonly QueryKey[];
  /** Extra terminal-state handling (success toasts, downloads, ...). */
  onTerminal?: (job: Job) => void;
}

/**
 * "Queue a job, watch it, report failures" -- the workflow behind every
 * compute button in the app. Wraps useJobPolling with the surrounding bits
 * that were previously hand-rolled identically in eight components: the
 * jobId state, the terminal-state invalidation, the failure toast, and the
 * `running` flag driving button spinners.
 *
 * `running` deliberately covers the *whole* round trip -- from the click,
 * through queueing, until the job reports terminal. Deriving it from polled
 * job data alone (as the copies did) leaves the button briefly idle-looking
 * between the click and the first poll response.
 */
export function useJobRunner<TArgs = void>(options: JobRunnerOptions<TArgs>) {
  const [jobId, setJobId] = useState<number | undefined>();
  const [starting, setStarting] = useState(false);
  const queryClient = useQueryClient();

  // Callers pass a fresh options object every render; keeping it in a ref
  // means run/handleTerminal stay referentially stable without every call
  // site having to useCallback its own handlers.
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const handleTerminal = useCallback(
    (job: Job) => {
      const { invalidate, failTitle, onTerminal } = optionsRef.current;
      for (const queryKey of invalidate ?? []) {
        queryClient.invalidateQueries({ queryKey });
      }
      if (job.state === "failed") {
        notifications.show({ color: "red", title: failTitle, message: job.progress_message ?? "" });
      }
      onTerminal?.(job);
    },
    [queryClient],
  );

  const { data: job, isError } = useJobPolling(jobId, handleTerminal);

  /** Resolves with the queued job, or undefined if queueing failed (the
   * error is already on screen as a toast by then). */
  const run = useCallback(async (args: TArgs): Promise<Job | undefined> => {
    setStarting(true);
    try {
      const queued = await optionsRef.current.start(args);
      setJobId(queued.id);
      return queued;
    } catch (err) {
      showApiError(optionsRef.current.startFailTitle, err);
      return undefined;
    } finally {
      setStarting(false);
    }
  }, []);

  // isError guards against polling a job that no longer exists (deleted from
  // the Jobs panel mid-run) leaving the button spinning forever.
  const settled = isError || (job != null && TERMINAL_JOB_STATES.has(job.state));
  const running = starting || (jobId != null && !settled);

  return { run, job, running };
}
