import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { computeHfo, getHfoResult } from "../endpoints";
import type { HfoComputeParams } from "../endpoints";

export function useComputeHfo(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ edfArtifactId, params }: { edfArtifactId: number; params: HfoComputeParams }) =>
      computeHfo(subjectId, edfArtifactId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useHfoResult(subjectId: number | undefined, edfArtifactId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["hfo-result", subjectId, edfArtifactId],
    queryFn: () => getHfoResult(subjectId!, edfArtifactId!),
    enabled: enabled && subjectId != null && edfArtifactId != null,
    retry: false,
  });
}
