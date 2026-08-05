import { exportPatient } from "../endpoints";
import { qk } from "../queryKeys";
import { makeMutation } from "./factories";

/** Kick off the whole-patient export job. Progress is watched via the Jobs
 * panel / useJobPolling on the returned job id. */
export const useExportPatient = makeMutation((subjectId: number) => exportPatient(subjectId), [qk.jobs()]);
