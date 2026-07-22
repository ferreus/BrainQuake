import { useEffect, useMemo } from "react";
import { useDebouncedValue } from "@mantine/hooks";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { getEdfMeta, getEdfWindow } from "../endpoints";
import type { EdfWindowParams } from "../endpoints";

// Wheel-scrolling the EEG canvas dispatches PAN_TIME on every tick; without
// debouncing, a fast scroll fires a burst of overlapping filtered-window
// requests (each re-running filtfilt server-side). Settling here first cuts
// that down to one request per pause.
const PAN_DEBOUNCE_MS = 150;

export function useEdfMeta(subjectId: number | undefined, edfArtifactId: number | undefined) {
  return useQuery({
    queryKey: ["edf-meta", subjectId, edfArtifactId],
    queryFn: () => getEdfMeta(subjectId!, edfArtifactId!),
    enabled: subjectId != null && edfArtifactId != null,
    staleTime: Infinity,
    retry: false,
  });
}

function edfWindowQueryKey(subjectId: number | undefined, edfArtifactId: number | undefined, params: EdfWindowParams) {
  return [
    "edf-window",
    subjectId,
    edfArtifactId,
    params.start,
    params.end,
    params.channels?.join(",") ?? "*",
    params.bandLow,
    params.bandHigh,
  ] as const;
}

export function useEdfWindow(
  subjectId: number | undefined,
  edfArtifactId: number | undefined,
  params: EdfWindowParams,
  enabled = true,
) {
  // Reuse the same object reference across renders whenever the actual
  // params are unchanged -- useDebouncedValue resets its timer on every
  // *reference* change, and the caller passes a fresh object literal each
  // render.
  const stableParams = useMemo(
    () => params,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params.start, params.end, params.channels, params.bandLow, params.bandHigh],
  );
  const [debounced] = useDebouncedValue(stableParams, PAN_DEBOUNCE_MS);
  const queryClient = useQueryClient();
  const active = enabled && subjectId != null && edfArtifactId != null;

  const query = useQuery({
    queryKey: edfWindowQueryKey(subjectId, edfArtifactId, debounced),
    queryFn: () => getEdfWindow(subjectId!, edfArtifactId!, debounced),
    enabled: active,
    placeholderData: keepPreviousData,
    retry: false,
  });

  // Once a window settles, warm the cache for the two adjacent (one-page-
  // over) windows so continued panning in the same direction is instant.
  useEffect(() => {
    if (!active) return;
    const span = debounced.end - debounced.start;
    if (span <= 0) return;

    for (const step of [span, -span]) {
      const start = Math.max(0, debounced.start + step);
      if (start === debounced.start) continue; // clamped -- e.g. panning back past t=0
      const neighbor: EdfWindowParams = { ...debounced, start, end: start + span };
      queryClient.prefetchQuery({
        queryKey: edfWindowQueryKey(subjectId, edfArtifactId, neighbor),
        queryFn: () => getEdfWindow(subjectId!, edfArtifactId!, neighbor),
      });
    }
  }, [active, subjectId, edfArtifactId, debounced, queryClient]);

  return query;
}
