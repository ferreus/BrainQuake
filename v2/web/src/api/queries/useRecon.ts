import { runRecon } from "../endpoints";
import { qk } from "../queryKeys";
import { makeMutation } from "./factories";
import type { ReconType } from "../types";

/** ageMonths is required by the server for infant-surfer only (see
 * routers/recon.py's 400 on a missing age_months) and ignored otherwise. */
export const useRunRecon = makeMutation(
  ({
    subjectId,
    reconType,
    ageMonths,
  }: {
    subjectId: number;
    reconType: ReconType;
    ageMonths?: number | null;
  }) => runRecon(subjectId, reconType, ageMonths),
  [qk.jobs()],
);
