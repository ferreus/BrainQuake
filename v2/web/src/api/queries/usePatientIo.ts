import { useMutation, useQueryClient } from "@tanstack/react-query";
import { exportPatient } from "../endpoints";

/** Kick off the whole-patient export job. Progress is watched via the Jobs
 * panel / useJobPolling on the returned job id. */
export function useExportPatient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (subjectId: number) => exportPatient(subjectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}
