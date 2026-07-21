import { useQuery } from "@tanstack/react-query";
import { apiGetBinary } from "../client";
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
