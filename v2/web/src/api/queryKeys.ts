// Single source of truth for every TanStack query key in the app. Both the
// hooks in api/queries/ and any component that invalidates directly must go
// through here -- the keys used to be hand-typed as raw string literals in
// 20+ places, so a typo (or a hook quietly changing its key shape) silently
// broke invalidation with no type error to catch it.
//
// Note the deliberate prefix/full pairs: TanStack matches invalidations by
// key prefix, and several call sites rely on that. `jobs()` invalidates every
// per-subject jobs list keyed by `jobsList()`, and `surface()` invalidates
// both hemispheres keyed by `surfaceMesh()`.
import type { EdfWindowParams } from "./endpoints";

type Id = number | undefined;

export const qk = {
  subjects: () => ["subjects"] as const,
  subject: (subjectId: Id) => ["subject", subjectId] as const,

  /** Prefix -- invalidates every jobs list regardless of subject. */
  jobs: () => ["jobs"] as const,
  jobsList: (subjectId?: number) => ["jobs", subjectId ?? "all"] as const,
  job: (jobId: Id) => ["job", jobId] as const,
  jobLog: (jobId: Id) => ["job-log", jobId] as const,

  artifacts: (subjectId: Id) => ["artifacts", subjectId] as const,

  /** Prefix -- invalidates both hemispheres. */
  surface: (subjectId: Id) => ["surface", subjectId] as const,
  surfaceMesh: (subjectId: Id, hemi: "lh" | "rh") => ["surface", subjectId, hemi] as const,

  chnXyz: (subjectId: Id) => ["chn-xyz", subjectId] as const,
  labelsSummary: (subjectId: Id) => ["labels-summary", subjectId] as const,

  edfMeta: (subjectId: Id, edfArtifactId: Id) => ["edf-meta", subjectId, edfArtifactId] as const,
  edfWindow: (subjectId: Id, edfArtifactId: Id, params: EdfWindowParams) =>
    [
      "edf-window",
      subjectId,
      edfArtifactId,
      params.start,
      params.end,
      params.channels?.join(",") ?? "*",
      params.bandLow,
      params.bandHigh,
    ] as const,

  eiResult: (subjectId: Id, edfArtifactId: Id) => ["ei-result", subjectId, edfArtifactId] as const,
  hfoResult: (subjectId: Id, edfArtifactId: Id) => ["hfo-result", subjectId, edfArtifactId] as const,
  sozResult: (subjectId: Id) => ["soz-result", subjectId] as const,
};
