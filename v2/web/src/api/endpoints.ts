// Thin REST resource functions, one per v2/server endpoint used so far --
// mirrors v2/client/api_client.py's method surface so the two clients stay
// easy to cross-reference.
import { apiDelete, apiGet, apiGetBinary, apiGetText, apiPost, apiPut, uploadFileWithProgress } from "./client";
import { getBaseUrl } from "./serverConfig";
import { parseEdfWindowBinary } from "../lib/parseEdfWindowBinary";
import type { Artifact, Job, ReconType, Subject } from "./types";

export function listSubjects(): Promise<Subject[]> {
  return apiGet<Subject[]>("/subjects");
}

export function createSubject(name: string, reconType?: ReconType): Promise<Subject> {
  return apiPost<Subject>("/subjects", { name, recon_type: reconType });
}

export function getSubject(id: number): Promise<Subject> {
  return apiGet<Subject>(`/subjects/${id}`);
}

export function deleteSubject(id: number): Promise<{ message: string }> {
  return apiDelete(`/subjects/${id}`);
}

export function listArtifacts(subjectId: number, kind?: string): Promise<Artifact[]> {
  const qs = kind ? `?kind=${encodeURIComponent(kind)}` : "";
  return apiGet<Artifact[]>(`/subjects/${subjectId}/artifacts${qs}`);
}

export function deleteArtifact(artifactId: number): Promise<{ message: string }> {
  return apiDelete(`/artifacts/${artifactId}`);
}

// --- Whole-patient export / import ---------------------------------------

/** Queue a job that zips the subject's entire on-disk footprint. */
export function exportPatient(subjectId: number): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/export`);
}

/** Absolute URL of the latest completed export archive -- point an <a> at it
 * (or window.location) to let the browser handle the file download. */
export function patientExportDownloadUrl(subjectId: number): string {
  return `${getBaseUrl()}/subjects/${subjectId}/export/download`;
}

export interface ImportResult {
  subject: Subject;
  job: Job;
}

/** Multipart-upload a previously exported patient zip. The server reads the
 * subject name from the archive manifest, creates the subject, and queues an
 * import job; returns both. Progress is byte-level upload progress. */
export function importPatient(
  file: File,
  onProgress?: (fraction: number) => void,
): { promise: Promise<ImportResult>; cancel: () => void } {
  return uploadFileWithProgress<ImportResult>(`/subjects/import`, file, null, onProgress);
}

export function listJobs(params?: { subjectId?: number; state?: string }): Promise<Job[]> {
  const qs = new URLSearchParams();
  if (params?.subjectId != null) qs.set("subject_id", String(params.subjectId));
  if (params?.state) qs.set("state", params.state);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiGet<Job[]>(`/jobs${suffix}`);
}

export function getJob(id: number): Promise<Job> {
  return apiGet<Job>(`/jobs/${id}`);
}

export function getJobLog(id: number): Promise<string> {
  return apiGetText(`/jobs/${id}/log`);
}

export function cancelJob(id: number): Promise<{ message: string; job: Job }> {
  return apiPost(`/jobs/${id}/cancel`);
}

export function deleteJob(id: number): Promise<{ message: string }> {
  return apiDelete(`/jobs/${id}`);
}

export function runRecon(subjectId: number, reconType: ReconType): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/recon`, { recon_type: reconType });
}

export function rebuildSurface(subjectId: number): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/surface/rebuild`);
}

export function registerCt(subjectId: number): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/electrodes/register-ct`);
}

export interface DetectParams {
  K: number;
  threshold_pct: number;
  erosion_iterations: number;
}

export function detectElectrodes(subjectId: number, params: DetectParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/electrodes/detect`, params);
}

export interface LabelCluster {
  label: number;
  voxel_count: number;
  centroid: [number, number, number];
}

export interface LabelsSummary {
  K: number;
  clusters: LabelCluster[];
}

export function getLabelsSummary(subjectId: number): Promise<LabelsSummary> {
  return apiGet<LabelsSummary>(`/subjects/${subjectId}/electrodes/labels-summary`);
}

export function updateLabels(subjectId: number, excludeLabels: number[]): Promise<{ K: number }> {
  return apiPut(`/subjects/${subjectId}/electrodes/labels`, { exclude_labels: excludeLabels });
}

export interface SegmentParams {
  numMax?: number;
  diameterSize?: number;
  spacing?: number;
  gap?: number;
}

export function segmentElectrodes(subjectId: number, params: SegmentParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/electrodes/segment`, params);
}

export type ChnXyz = Record<string, number[][]>;

export function getChnXyz(subjectId: number): Promise<ChnXyz> {
  return apiGet<ChnXyz>(`/subjects/${subjectId}/electrodes/chn-xyz`);
}

export function getContacts(subjectId: number, label: string): Promise<number[][]> {
  return apiGet<number[][]>(`/subjects/${subjectId}/electrodes/contacts/${encodeURIComponent(label)}`);
}

export interface EdfMeta {
  fs: number;
  n_samples: number;
  duration_sec: number;
  channels: string[];
  amplitude_range: { min: number; max: number };
}

export function getEdfMeta(subjectId: number, edfArtifactId: number): Promise<EdfMeta> {
  return apiGet<EdfMeta>(`/subjects/${subjectId}/edf/${edfArtifactId}/meta`);
}

export interface EdfWindowParams {
  start: number;
  end: number;
  channels?: string[];
  bandLow?: number;
  bandHigh?: number;
}

export interface EdfWindow {
  fs: number;
  start: number;
  end: number;
  channels: string[];
  filtered: boolean;
  band_low: number | null;
  band_high: number | null;
  /** data[channelIndex] is that channel's samples for the window. */
  data: Float32Array[];
}

// Binary (not JSON) response -- see app/services/edf.py's pack_edf_window
// and v2/web/src/lib/parseEdfWindowBinary.ts. This endpoint is on the hot
// path for every pan/zoom/filter-toggle of the EEG canvas, and JSON floats
// were a measurable chunk of that round trip.
export async function getEdfWindow(
  subjectId: number,
  edfArtifactId: number,
  params: EdfWindowParams,
): Promise<EdfWindow> {
  const qs = new URLSearchParams();
  qs.set("start", String(params.start));
  qs.set("end", String(params.end));
  if (params.channels?.length) qs.set("channels", params.channels.join(","));
  if (params.bandLow != null) qs.set("band_low", String(params.bandLow));
  if (params.bandHigh != null) qs.set("band_high", String(params.bandHigh));
  const buf = await apiGetBinary(`/subjects/${subjectId}/edf/${edfArtifactId}/window?${qs.toString()}`);
  const parsed = parseEdfWindowBinary(buf);
  return {
    fs: parsed.fs,
    start: parsed.start,
    end: parsed.end,
    channels: parsed.channels,
    filtered: parsed.filtered,
    band_low: parsed.bandLow,
    band_high: parsed.bandHigh,
    data: parsed.data,
  };
}

export interface EiComputeParams {
  baseline_start: number;
  baseline_end: number;
  target_start: number;
  target_end: number;
  band_low?: number;
  band_high?: number;
}

export function computeEi(subjectId: number, edfArtifactId: number, params: EiComputeParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/ictal/${edfArtifactId}/ei`, params);
}

// Mirrors v2/client/api_client.py's _RETRY_DISPATCH: every job type's
// params_json already carries everything its original POST body needed, so
// retrying is just re-issuing the same call with those same params. The
// hfo/soz entries POST to the server routes directly because the web client
// has no dedicated wrappers for them yet (Phase 4/5 features).
type RetryFn = (subjectId: number, p: Record<string, unknown>) => Promise<Job>;

const RETRY_DISPATCH: Record<string, RetryFn> = {
  recon: (subjectId, p) => runRecon(subjectId, (p.recon_type as ReconType) ?? "recon-all"),
  ct_register: (subjectId) => registerCt(subjectId),
  elec_detect: (subjectId, p) => detectElectrodes(subjectId, p as unknown as DetectParams),
  elec_segment: (subjectId, p) => segmentElectrodes(subjectId, p as SegmentParams),
  ei_compute: (subjectId, p) => {
    const { edf_artifact_id, ...params } = p;
    return computeEi(subjectId, edf_artifact_id as number, params as unknown as EiComputeParams);
  },
  hfo_compute: (subjectId, p) => {
    const { edf_artifact_id, ...params } = p;
    return apiPost<Job>(`/subjects/${subjectId}/interictal/${edf_artifact_id}/hfo`, params);
  },
  soz_fuse: (subjectId, p) => apiPost<Job>(`/subjects/${subjectId}/soz/fuse`, p),
};

export function retryJob(job: Job): Promise<Job> {
  const dispatch = RETRY_DISPATCH[job.job_type];
  if (!dispatch) {
    return Promise.reject(new Error(`Don't know how to retry job type '${job.job_type}'`));
  }
  return dispatch(job.subject_id, job.params_json ?? {});
}

export interface EiResult {
  chn_names: string[];
  ei: number[];
  hfer: number[];
  onset_rank: number[];
}

export function getEiResult(subjectId: number, edfArtifactId: number): Promise<EiResult> {
  return apiGet<EiResult>(`/subjects/${subjectId}/ictal/${edfArtifactId}/ei-result`);
}
