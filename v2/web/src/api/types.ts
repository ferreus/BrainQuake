// Mirrors of v2/server/app/schemas/*.py + models/*.py. job_type/state are
// free-text strings server-side (no DB-level enum), so we type them as
// string unions for editor/compile-time help but the server does not
// validate against them.

export type JobState = "queued" | "running" | "finished" | "failed" | "cancelled";

export const TERMINAL_JOB_STATES: ReadonlySet<JobState> = new Set([
  "finished",
  "failed",
  "cancelled",
]);

export type JobType =
  | "recon"
  | "ct_register"
  | "elec_detect"
  | "elec_segment"
  | "ei_compute"
  | "hfo_compute"
  | "soz_fuse"
  | "export_patient"
  | "import_patient";

export type ReconType = "recon-all" | "fast-surfer" | "infant-surfer";

export const RECON_TYPES: ReconType[] = ["recon-all", "fast-surfer", "infant-surfer"];

export interface Subject {
  id: number;
  name: string;
  recon_type: ReconType | null;
  subject_dir: string | null;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: number;
  subject_id: number;
  job_type: JobType | string;
  state: JobState;
  progress_pct: number;
  progress_message: string | null;
  params_json: Record<string, unknown> | null;
  log_path: string | null;
  pid: number | null;
  host: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: number;
  subject_id: number;
  job_id: number | null;
  kind: string;
  rel_path: string;
  meta_json: Record<string, unknown> | null;
  created_at: string;
}

export type UploadFileType = "t1" | "ct" | "edf" | "zip";
