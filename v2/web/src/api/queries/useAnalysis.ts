import { useMemo } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
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

export const DEFAULT_AGGREGATE_TOP_N = 20;

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


export interface AnalysisRun {
  process: string;
  /** The result file itself -- what the SOZ run picker selects and deletes. */
  artifactId: number;
  recording: string;
  /** The mark chosen as t=0 ("SZ 2P"), or the raw onset when it was manual. */
  label: string;
  nChannels: number;
  medianR2: number | null;
}

function runLabel(label: string | null, onsetS: number | null): string {
  if (label) return label;
  if (onsetS != null) return `t=${onsetS.toFixed(1)}s`;
  return "whole recording";
}

/** Every finished run of every process, flattened -- what the SOZ fusion can
 * draw from. top_n only affects the aggregate's shaft table, which this ignores. */
export function useAnalysisRuns(subjectId: number, processIds: string[]) {
  const results = useQueries({
    queries: processIds.map((process) => ({
      queryKey: analysisAggregateQueryKey(subjectId, process, DEFAULT_AGGREGATE_TOP_N),
      queryFn: () => getAnalysisAggregate(subjectId, process, DEFAULT_AGGREGATE_TOP_N),
      retry: false,
    })),
  });

  const isLoading = results.some((r) => r.isLoading);
  const stamp = results.map((r) => r.dataUpdatedAt).join(",");
  const runs = useMemo(
    () =>
      results.flatMap((r, i) =>
        (r.data?.runs ?? []).map((run) => ({
          process: processIds[i],
          artifactId: run.artifact_id,
          recording: run.recording,
          label: runLabel(run.label, run.onset_s),
          nChannels: run.n_channels,
          medianR2: run.median_r2,
        })),
      ),
    // Keyed on when each query last resolved: `results` is a new array every render.
    [stamp, processIds.join(",")],
  );

  return { runs, isLoading };
}
