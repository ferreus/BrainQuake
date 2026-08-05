import { useQuery } from "@tanstack/react-query";
import { apiGetBinary } from "../client";
import { rebuildSurface } from "../endpoints";
import { qk } from "../queryKeys";
import { makeSubjectMutation } from "./factories";
import { parseSurfaceBinary } from "../../lib/parseSurfaceBinary";
import type { ParsedSurface } from "../../lib/parseSurfaceBinary";
import type { Job } from "../types";

export function useSurfaceMesh(subjectId: number | undefined, hemi: "lh" | "rh") {
  return useQuery<ParsedSurface>({
    queryKey: qk.surfaceMesh(subjectId, hemi),
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
export const useRebuildSurface = makeSubjectMutation<void, Job>(rebuildSurface, () => [qk.jobs()]);
