import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiGetBinary } from "../client";
import { rebuildSurface } from "../endpoints";
import { parseSurfaceBinary } from "../../lib/parseSurfaceBinary";
import type { ParsedSurface } from "../../lib/parseSurfaceBinary";

export function useSurfaceMesh(subjectId: number | undefined, hemi: "lh" | "rh") {
  return useQuery<ParsedSurface>({
    queryKey: ["surface", subjectId, hemi],
    queryFn: async () => {
      const buf = await apiGetBinary(`/subjects/${subjectId}/surface/${hemi}`);
      return parseSurfaceBinary(buf);
    },
    enabled: subjectId != null,
    staleTime: Infinity,
    retry: false,
  });
}

/** POST .../surface/rebuild: (re)generates the cached lh/rh mesh binaries
 * from surf/{lh,rh}.pial. Recon jobs already do this once on success (see
 * app/services/recon.py), but a subject reconned before that step existed --
 * or reconned outside the API entirely -- has no cache yet, and
 * useSurfaceMesh 404s silently. This is the UI's escape hatch for that. */
export function useRebuildSurface(subjectId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => rebuildSurface(subjectId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
}
