import { useMutation, useQueryClient } from "@tanstack/react-query";
import { runRecon } from "../endpoints";
import type { ReconType } from "../types";

export function useRunRecon() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      subjectId,
      reconType,
      ageMonths,
    }: {
      subjectId: number;
      reconType: ReconType;
      ageMonths?: number | null;
    }) => runRecon(subjectId, reconType, ageMonths),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
