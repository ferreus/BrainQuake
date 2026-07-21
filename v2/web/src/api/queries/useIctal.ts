import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { computeEi, getEiResult } from "../endpoints";
import type { EiComputeParams } from "../endpoints";

export function useComputeEi(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ edfArtifactId, params }: { edfArtifactId: number; params: EiComputeParams }) =>
      computeEi(subjectId, edfArtifactId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
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
