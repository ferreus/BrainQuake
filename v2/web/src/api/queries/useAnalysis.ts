import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AnalysisRunItem } from "../endpoints";
import { getAnalysisAggregate, getFragilityResult, runAnalysis } from "../endpoints";

export function useRunAnalysis(subjectId: number, process: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { params: Record<string, unknown>; runs: AnalysisRunItem[] }) =>
      runAnalysis(subjectId, process, vars.params, vars.runs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function fragilityResultQueryKey(
  subjectId: number, edfArtifactId?: number, runKey?: string,
) {
  return ["fragility-result", subjectId, edfArtifactId, runKey ?? ""] as const;
}

export function useFragilityResult(
  subjectId: number, edfArtifactId?: number, runKey?: string, enabled = true,
) {
  return useQuery({
    queryKey: fragilityResultQueryKey(subjectId, edfArtifactId, runKey),
    queryFn: () => getFragilityResult(subjectId, edfArtifactId as number, runKey),
    enabled: enabled && edfArtifactId != null,
    retry: false,
  });
}

export function analysisAggregateQueryKey(subjectId: number, process: string, topN: number) {
  return ["analysis-aggregate", subjectId, process, topN] as const;
}

export function useAnalysisAggregate(subjectId: number, process: string, topN: number) {
  return useQuery({
    queryKey: analysisAggregateQueryKey(subjectId, process, topN),
    queryFn: () => getAnalysisAggregate(subjectId, process, topN),
    retry: false,
  });
}
