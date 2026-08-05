import {
  deleteArtifact,
  detectElectrodes,
  getLabelsSummary,
  listArtifacts,
  registerCt,
  segmentElectrodes,
  updateLabels,
} from "../endpoints";
import { qk } from "../queryKeys";
import { makeSubjectMutation, makeSubjectQuery } from "./factories";
import type { Job } from "../types";

export const useArtifacts = makeSubjectQuery(listArtifacts, qk.artifacts);

export const useLabelsSummary = makeSubjectQuery(getLabelsSummary, qk.labelsSummary, { retry: false });

// deleteArtifact is keyed by artifact id, not subject id -- the subject id
// this hook is built with is only used to invalidate the right artifact list.
export const useDeleteArtifact = makeSubjectMutation(
  (_subjectId: number, artifactId: number) => deleteArtifact(artifactId),
  (subjectId) => [qk.artifacts(subjectId)],
);

export const useRegisterCt = makeSubjectMutation<void, Job>(registerCt, () => [qk.jobs()]);

export const useDetectElectrodes = makeSubjectMutation(detectElectrodes, () => [qk.jobs()]);

export const useSegmentElectrodes = makeSubjectMutation(segmentElectrodes, () => [qk.jobs()]);

export const useUpdateLabels = makeSubjectMutation(updateLabels, (subjectId) => [qk.labelsSummary(subjectId)]);
