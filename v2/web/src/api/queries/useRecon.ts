import { useMutation, useQueryClient } from "@tanstack/react-query";
import { runRecon } from "../endpoints";
import type { ReconType } from "../types";

export function useRunRecon() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ subjectId, reconType }: { subjectId: number; reconType: ReconType }) =>
      runRecon(subjectId, reconType),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
