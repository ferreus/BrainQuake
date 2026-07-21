// Thin REST resource functions, one per v2/server endpoint used so far --
// mirrors v2/client/api_client.py's method surface so the two clients stay
// easy to cross-reference.
import { apiDelete, apiGet, apiGetText, apiPost, apiPut } from "./client";
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
  units: string;
  filtered: boolean;
  band_low: number | null;
  band_high: number | null;
  data: number[][];
}

export function getEdfWindow(subjectId: number, edfArtifactId: number, params: EdfWindowParams): Promise<EdfWindow> {
  const qs = new URLSearchParams();
  qs.set("start", String(params.start));
  qs.set("end", String(params.end));
  if (params.channels?.length) qs.set("channels", params.channels.join(","));
  if (params.bandLow != null) qs.set("band_low", String(params.bandLow));
  if (params.bandHigh != null) qs.set("band_high", String(params.bandHigh));
  return apiGet<EdfWindow>(`/subjects/${subjectId}/edf/${edfArtifactId}/window?${qs.toString()}`);
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

export interface EiResult {
  chn_names: string[];
  ei: number[];
  hfer: number[];
  onset_rank: number[];
}

export function getEiResult(subjectId: number, edfArtifactId: number): Promise<EiResult> {
  return apiGet<EiResult>(`/subjects/${subjectId}/ictal/${edfArtifactId}/ei-result`);
}
