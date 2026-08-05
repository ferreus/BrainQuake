// Hook factories for the two shapes that made up most of api/queries/: a
// mutation that just invalidates some keys on success, and a query that's
// disabled until its id(s) are known. Each hook built from these used to be
// 7-9 lines of identical boilerplate differing only in the endpoint function
// and the invalidated keys.
//
// Hooks with genuinely custom behavior (useJobs, useLastJob, useJobLog,
// useJobPolling, useEdfWindow, useRetryJob) stay hand-written -- they do more
// than fit through here, and forcing them into a factory would cost more than
// it saves.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient, QueryKey } from "@tanstack/react-query";

function invalidateAll(queryClient: QueryClient, keys: readonly QueryKey[]) {
  for (const queryKey of keys) {
    queryClient.invalidateQueries({ queryKey });
  }
}

/** Mutation hook taking no construction argument. */
export function makeMutation<TVars, TData>(
  mutationFn: (vars: TVars) => Promise<TData>,
  invalidates: readonly QueryKey[],
) {
  return function useGeneratedMutation() {
    const queryClient = useQueryClient();
    return useMutation({
      mutationFn,
      onSuccess: () => invalidateAll(queryClient, invalidates),
    });
  };
}

/**
 * Mutation hook of the `useThing(subjectId)` shape. `mutationFn` receives the
 * subject id the hook was built with plus the mutation's own variables, so
 * most endpoint functions can be passed straight through unwrapped.
 * Pass TVars explicitly as `void` for endpoints that take only a subject id --
 * TS can't infer it from a function that declares fewer parameters.
 */
export function makeSubjectMutation<TVars, TData>(
  mutationFn: (subjectId: number, vars: TVars) => Promise<TData>,
  invalidates: (subjectId: number) => readonly QueryKey[],
) {
  return function useGeneratedSubjectMutation(subjectId: number) {
    const queryClient = useQueryClient();
    return useMutation({
      mutationFn: (vars: TVars) => mutationFn(subjectId, vars),
      onSuccess: () => invalidateAll(queryClient, invalidates(subjectId)),
    });
  };
}

/** Per-query overrides. Everything else (key, fn, enabled) is derived. */
interface QueryTuning {
  staleTime?: number;
  retry?: boolean;
}

/** Query hook of the `useThing(subjectId, enabled?)` shape. */
export function makeSubjectQuery<TData>(
  queryFn: (subjectId: number) => Promise<TData>,
  keyFn: (subjectId: number | undefined) => QueryKey,
  tuning: QueryTuning = {},
) {
  return function useGeneratedSubjectQuery(subjectId: number | undefined, enabled = true) {
    return useQuery({
      queryKey: keyFn(subjectId),
      queryFn: () => queryFn(subjectId!),
      enabled: enabled && subjectId != null,
      ...tuning,
    });
  };
}

/** Query hook of the `useThing(subjectId, edfArtifactId, enabled?)` shape. */
export function makeEdfQuery<TData>(
  queryFn: (subjectId: number, edfArtifactId: number) => Promise<TData>,
  keyFn: (subjectId: number | undefined, edfArtifactId: number | undefined) => QueryKey,
  tuning: QueryTuning = {},
) {
  return function useGeneratedEdfQuery(
    subjectId: number | undefined,
    edfArtifactId: number | undefined,
    enabled = true,
  ) {
    return useQuery({
      queryKey: keyFn(subjectId, edfArtifactId),
      queryFn: () => queryFn(subjectId!, edfArtifactId!),
      enabled: enabled && subjectId != null && edfArtifactId != null,
      ...tuning,
    });
  };
}
