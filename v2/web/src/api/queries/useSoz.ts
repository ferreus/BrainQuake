import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fuseSoz, getSozResult } from "../endpoints";
import type { SozFuseParams } from "../endpoints";

export function useFuseSoz(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: SozFuseParams = {}) => fuseSoz(subjectId, params),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}

export function useSozResult(subjectId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["soz-result", subjectId],
    queryFn: () => getSozResult(subjectId!),
    enabled: enabled && subjectId != null,
    retry: false,
  });
}
