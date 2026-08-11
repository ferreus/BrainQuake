import { useQuery } from "@tanstack/react-query";
import { getRecordingParams } from "../endpoints";

export function recordingParamsQueryKey(subjectId: number | undefined, edfArtifactId: number | undefined) {
  return ["recording-params", subjectId, edfArtifactId] as const;
}

/** The recording's saved ictal/interictal params and parsed annotations.
 * Not `staleTime: Infinity` like useEdfMeta -- params change every time a
 * compute job is submitted, and the compute forms invalidate this key
 * explicitly right after submitting rather than relying on a refetch. */
export function useRecordingParams(subjectId: number | undefined, edfArtifactId: number | undefined) {
  return useQuery({
    queryKey: recordingParamsQueryKey(subjectId, edfArtifactId),
    queryFn: () => getRecordingParams(subjectId!, edfArtifactId!),
    enabled: subjectId != null && edfArtifactId != null,
    retry: false,
  });
}
