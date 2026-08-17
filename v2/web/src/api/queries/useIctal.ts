import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { computeEi, getBipolarPreview, getEiResult } from "../endpoints";
import type { EiComputeParams } from "../endpoints";

export function useComputeEi(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ edfArtifactId, params }: { edfArtifactId: number; params: EiComputeParams }) =>
      computeEi(subjectId, edfArtifactId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

/** What a bipolar montage would build for this recording, so the form can show
 * it before a job is queued rather than failing after one. */
export function useBipolarPreview(
  subjectId: number,
  edfArtifactId: number | undefined,
  remainChns: string[],
  enabled: boolean,
) {
  return useQuery({
    queryKey: ["bipolar-preview", subjectId, edfArtifactId, remainChns],
    queryFn: () => getBipolarPreview(subjectId, edfArtifactId!, remainChns),
    enabled: enabled && edfArtifactId != null,
    retry: false,
  });
}

export function useEiResult(subjectId: number | undefined, edfArtifactId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["ei-result", subjectId, edfArtifactId],
    queryFn: () => getEiResult(subjectId!, edfArtifactId!),
    enabled: enabled && subjectId != null && edfArtifactId != null,
    retry: false,
  });
}
