import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getEdfMeta, getEdfWindow } from "../endpoints";
import type { EdfWindowParams } from "../endpoints";

export function useEdfMeta(subjectId: number | undefined, edfArtifactId: number | undefined) {
  return useQuery({
    queryKey: ["edf-meta", subjectId, edfArtifactId],
    queryFn: () => getEdfMeta(subjectId!, edfArtifactId!),
    enabled: subjectId != null && edfArtifactId != null,
    staleTime: Infinity,
    retry: false,
  });
}

export function useEdfWindow(
  subjectId: number | undefined,
  edfArtifactId: number | undefined,
  params: EdfWindowParams,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "edf-window",
      subjectId,
      edfArtifactId,
      params.start,
      params.end,
      params.channels?.join(",") ?? "*",
      params.bandLow,
      params.bandHigh,
    ],
    queryFn: () => getEdfWindow(subjectId!, edfArtifactId!, params),
    enabled: enabled && subjectId != null && edfArtifactId != null,
    placeholderData: keepPreviousData,
    retry: false,
  });
}
