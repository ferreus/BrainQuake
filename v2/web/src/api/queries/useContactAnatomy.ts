import { useQuery } from "@tanstack/react-query";
import { getContactAnatomy } from "../endpoints";

/** 404s for a subject with no contacts or no FreeSurfer segmentation yet, both
 * of which are normal states of the electrodes tab rather than errors worth
 * retrying -- same retry: false as useChnXyz for the same reason. Not
 * staleTime: Infinity though: the server recomputes this from whatever is on
 * disk, so it must refetch after a re-import invalidates it. */
export function useContactAnatomy(subjectId: number | undefined, radiusMm?: number) {
  return useQuery({
    queryKey: ["contact-anatomy", subjectId, radiusMm ?? null],
    queryFn: () => getContactAnatomy(subjectId!, radiusMm),
    enabled: subjectId != null,
    retry: false,
  });
}
