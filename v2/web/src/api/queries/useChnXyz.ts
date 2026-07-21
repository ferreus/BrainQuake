import { useQuery } from "@tanstack/react-query";
import { getChnXyz } from "../endpoints";

export function useChnXyz(subjectId: number | undefined) {
  return useQuery({
    queryKey: ["chn-xyz", subjectId],
    queryFn: () => getChnXyz(subjectId!),
    enabled: subjectId != null,
    staleTime: Infinity,
    retry: false,
  });
}
