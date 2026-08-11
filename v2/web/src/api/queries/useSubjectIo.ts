import { useMutation, useQueryClient } from "@tanstack/react-query";
import { exportSubject } from "../endpoints";

/** Kick off the whole-subject export job. Progress is watched via the Jobs
 * panel / useJobPolling on the returned job id. */
export function useExportSubject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (subjectId: number) => exportSubject(subjectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
