import { useEffect, useRef } from "react";
import { useLastJob } from "./useJobs";

/**
 * Seeds a form from the params of the subject's last job of `jobType`, once.
 * Without this a reload silently resets every field to its hardcoded default
 * even though the values last submitted are sitting right there in the job's
 * params_json.
 *
 * Applied a single time per mount on purpose -- re-applying whenever the jobs
 * list refetches would stomp on whatever the user is currently typing.
 */
export function usePrefillFromLastJob<TParams>(
  subjectId: number | undefined,
  jobType: string,
  apply: (params: Partial<TParams>) => void,
) {
  const lastJob = useLastJob(subjectId, jobType);
  const applied = useRef(false);
  const applyRef = useRef(apply);
  applyRef.current = apply;

  useEffect(() => {
    if (applied.current || !lastJob?.params_json) return;
    applied.current = true;
    applyRef.current(lastJob.params_json as Partial<TParams>);
  }, [lastJob]);
}
