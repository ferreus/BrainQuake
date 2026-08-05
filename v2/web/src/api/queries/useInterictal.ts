import { computeHfo, getHfoResult } from "../endpoints";
import { qk } from "../queryKeys";
import { makeEdfQuery, makeSubjectMutation } from "./factories";
import type { HfoComputeParams } from "../endpoints";

export const useComputeHfo = makeSubjectMutation(
  (subjectId: number, { edfArtifactId, params }: { edfArtifactId: number; params: HfoComputeParams }) =>
    computeHfo(subjectId, edfArtifactId, params),
  () => [qk.jobs()],
);

export const useHfoResult = makeEdfQuery(getHfoResult, qk.hfoResult, { retry: false });
