import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  detectElectrodes,
  getLabelsSummary,
  listArtifacts,
  registerCt,
  segmentElectrodes,
  updateLabels,
} from "../endpoints";
import type { DetectParams, SegmentParams } from "../endpoints";

export function useArtifacts(subjectId: number | undefined) {
  return useQuery({
    queryKey: ["artifacts", subjectId],
    queryFn: () => listArtifacts(subjectId!),
    enabled: subjectId != null,
  });
}

export function useRegisterCt(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => registerCt(subjectId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useDetectElectrodes(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: DetectParams) => detectElectrodes(subjectId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useLabelsSummary(subjectId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["labels-summary", subjectId],
    queryFn: () => getLabelsSummary(subjectId!),
    enabled: enabled && subjectId != null,
    retry: false,
  });
}

export function useUpdateLabels(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (excludeLabels: number[]) => updateLabels(subjectId, excludeLabels),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["labels-summary", subjectId] }),
  });
}

export function useSegmentElectrodes(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: SegmentParams) => segmentElectrodes(subjectId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}
