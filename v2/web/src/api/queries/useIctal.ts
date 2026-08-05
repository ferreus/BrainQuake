import { computeEi, getEiResult } from "../endpoints";
import { qk } from "../queryKeys";
import { makeEdfQuery, makeSubjectMutation } from "./factories";
import type { EiComputeParams } from "../endpoints";

export const useComputeEi = makeSubjectMutation(
  (subjectId: number, { edfArtifactId, params }: { edfArtifactId: number; params: EiComputeParams }) =>
    computeEi(subjectId, edfArtifactId, params),
  () => [qk.jobs()],
);

export const useEiResult = makeEdfQuery(getEiResult, qk.eiResult, { retry: false });
