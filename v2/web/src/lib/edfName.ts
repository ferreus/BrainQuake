import type { Artifact } from "../api/types";

/** Name to show for a recording: the uploaded filename without path or
 * extension. Falls back to the stored path for artifacts uploaded before the
 * filename was recorded. */
export function edfDisplayName(artifact: Artifact): string {
  const raw = (artifact.meta_json?.original_filename as string) || artifact.rel_path || "";
  const base = raw.split(/[\\/]/).pop() ?? "";
  return base.replace(/\.edf$/i, "") || `#${artifact.id}`;
}
