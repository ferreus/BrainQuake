// Thin REST resource functions, one per v2/server endpoint used so far.
import { API_BASE, apiDelete, apiGet, apiGetBinary, apiGetText, apiPost, apiPut, uploadFileWithProgress } from "./client";
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

// --- Whole-subject export / import ---------------------------------------

/** Queue a job that zips the subject's entire on-disk footprint. */
export function exportSubject(subjectId: number): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/export`);
}

/** Absolute URL of the latest completed export archive -- point an <a> at it
 * (or window.location) to let the browser handle the file download. */
export function subjectExportDownloadUrl(subjectId: number): string {
  return `${API_BASE}/subjects/${subjectId}/export/download`;
}

export interface ImportResult {
  subject: Subject;
  job: Job;
}

/** Multipart-upload a previously exported subject zip. The server reads the
 * subject name from the archive manifest, creates the subject, and queues an
 * import job; returns both. Progress is byte-level upload progress. */
export function importSubject(
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

export function runRecon(subjectId: number, reconType: ReconType, ageMonths?: number | null): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/recon`, { recon_type: reconType, age_months: ageMonths });
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

export interface ImportContactItem {
  electrode: string;
  contact_index: number;
  /** FreeSurfer surface (tkreg) RAS -- same space ElectrodeSeg.resulting() writes contacts in. */
  x: number;
  y: number;
  z: number;
}

export interface ImportContactsParams {
  contacts?: ImportContactItem[];
  /** Raw electrode/contact_index/surfR/surfA/surfS CSV text -- parsed
   * server-side inside the elec_import job, not here, so a malformed file
   * still produces a real (failed) job with the parse error as its
   * progress_message instead of a client-side error with no job at all. */
  csvText?: string;
}

/** Bridges externally-resolved SEEG contacts (e.g. from a 3D Slicer .mrb --
 * see docs/seeg_slicer_contact_import_plan.md) straight to chnXyzDict/contact_txt,
 * bypassing register-ct/detect/segment entirely. Exactly one of
 * params.contacts / params.csvText must be set. */
export function importContacts(subjectId: number, params: ImportContactsParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/electrodes/import`, {
    contacts: params.contacts,
    csv_text: params.csvText,
  });
}

/** Uploads a raw 3D Slicer .mrb scene file; the returned artifact id is what
 * startSlicerMrbPreview takes. */
export function uploadMrb(
  subjectId: number,
  file: File,
  onProgress?: (fraction: number) => void,
): { promise: Promise<Artifact>; cancel: () => void } {
  return uploadFileWithProgress<Artifact>(`/subjects/${subjectId}/upload`, file, "mrb", onProgress);
}

/** Queues a job that parses an uploaded .mrb into a *preview* contacts list --
 * see docs/seeg_slicer_contact_import_plan.md and the server's
 * services/electrodes.parse_mrb for the auto-selection heuristics involved.
 * Never writes real contacts directly -- the preview must be reviewed
 * (getSlicerMrbPreview) then approved or rejected. */
export function startSlicerMrbPreview(subjectId: number, mrbArtifactId: number): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/electrodes/import/preview`, { mrb_artifact_id: mrbArtifactId });
}

export interface SlicerMrbDiagnostics {
  node_name: string;
  candidate_node_names: string[];
  n_points: number;
  n_electrodes: number;
  coordinate_system: string;
  transform_used: "none" | "forward" | "inverse";
  /** Fraction of contacts landing inside this subject's own brainmask --
   * the sanity signal for whether the auto-picked node/transform direction
   * is actually right. Low values deserve real scrutiny before approving. */
  in_brain_fraction: number;
  warnings: string[];
}

export interface SlicerMrbPreview {
  contacts: ImportContactItem[];
  diagnostics: SlicerMrbDiagnostics;
}

export function getSlicerMrbPreview(subjectId: number): Promise<SlicerMrbPreview> {
  return apiGet<SlicerMrbPreview>(`/subjects/${subjectId}/electrodes/import/preview`);
}

/** Writes the pending preview's contacts into the real chnXyzDict/contact_txt
 * and discards the preview. Synchronous (not a job) -- see the server
 * endpoint's docstring for why. */
export function approveSlicerMrbPreview(subjectId: number): Promise<{ n_contacts: number; n_electrodes: number }> {
  return apiPost(`/subjects/${subjectId}/electrodes/import/preview/approve`);
}

export function rejectSlicerMrbPreview(subjectId: number): Promise<{ message: string }> {
  return apiPost(`/subjects/${subjectId}/electrodes/import/preview/reject`);
}

/** Clears clusters (labels_npy) and contacts (chnXyzDict/contact_txt), from
 * either detect()+segment() or an import, so the electrodes tab can be redone
 * from scratch. */
export function deleteElectrodeContacts(subjectId: number): Promise<{ message: string }> {
  return apiDelete(`/subjects/${subjectId}/electrodes/contacts`);
}

export type ChnXyz = Record<string, number[][]>;

export function getChnXyz(subjectId: number): Promise<ChnXyz> {
  return apiGet<ChnXyz>(`/subjects/${subjectId}/electrodes/chn-xyz`);
}

export function getContacts(subjectId: number, label: string): Promise<number[][]> {
  return apiGet<number[][]>(`/subjects/${subjectId}/electrodes/contacts/${encodeURIComponent(label)}`);
}

export interface AnatomyLabel {
  label_id: number;
  label_name: string;
}

export interface ContactAnatomy {
  electrode: string;
  contact_index: number;
  /** `${electrode}${contact_index}`, e.g. "K'7" -- the key the 3D view and the
   * table both select on, so neither depends on the other's ordering. */
  name: string;
  x: number;
  y: number;
  z: number;
  voxel: [number, number, number];
  /** Label of the single voxel the contact centre falls in. null only when
   * out_of_volume. */
  label_id: number | null;
  label_name: string | null;
  /** Closest grey-matter structure within radius_mm, with its distance. null
   * when there is none that close -- i.e. the contact really is in white
   * matter, not just near a boundary. */
  nearest_structure: (AnatomyLabel & { distance_mm: number }) | null;
  /** Every label within radius_mm by volume fraction, biggest first. */
  neighborhood: (AnatomyLabel & { fraction: number })[];
  /** Present (true) only when the contact falls outside the segmentation --
   * a coordinate-space error rather than an anatomical finding. */
  out_of_volume?: boolean;
}

export interface ContactAnatomyResult {
  /** Which segmentation the labels came from, e.g. "mri/aparc+aseg.mgz".
   * aseg.mgz alone has no cortical parcellation, so it is worth showing. */
  segmentation: string;
  radius_mm: number;
  contacts: ContactAnatomy[];
}

/** Names the anatomical structure each contact sits in. Computed server-side
 * on demand from chnXyzDict + the subject's FreeSurfer segmentation, so it is
 * never stale after a re-import. */
export function getContactAnatomy(subjectId: number, radiusMm?: number): Promise<ContactAnatomyResult> {
  const qs = radiusMm != null ? `?radius_mm=${radiusMm}` : "";
  return apiGet<ContactAnatomyResult>(`/subjects/${subjectId}/electrodes/anatomy${qs}`);
}

export interface EdfMeta {
  fs: number;
  n_samples: number;
  duration_sec: number;
  channels: string[];
  /** Subset of `channels` whose names are not SEEG contacts (REF1, DC01, EKG1,
   * MARK, ...), classified server-side by the contact-naming convention so the
   * rule has one implementation. Excluded from the working set on load. */
  aux_channels: string[];
  /** Recording start as an ISO-8601 timestamp, or null when the EDF header
   * carries none. The clinical view labels its time axis from this; every other
   * time in the API is seconds from the start of the recording. */
  meas_date: string | null;
  amplitude_range: { min: number; max: number };
}

export function getEdfMeta(subjectId: number, edfArtifactId: number): Promise<EdfMeta> {
  return apiGet<EdfMeta>(`/subjects/${subjectId}/edf/${edfArtifactId}/meta`);
}

/** Uploads an ictal/interictal recording. The server rejects (409) a filename
 * this subject already has unless `overwrite`, which replaces the old
 * recording and everything derived from it. */
export function uploadEdf(
  subjectId: number,
  file: File,
  options?: { overwrite?: boolean },
  onProgress?: (fraction: number) => void,
): { promise: Promise<Artifact>; cancel: () => void } {
  const qs = `?file_type=edf${options?.overwrite ? "&overwrite=true" : ""}`;
  return uploadFileWithProgress<Artifact>(`/subjects/${subjectId}/upload${qs}`, file, null, onProgress);
}

export interface DeleteEdfResult {
  deleted_artifacts: number;
  deleted_jobs: number;
}

/** Deletes the recording plus its derived EI/HFO jobs, results and files. */
export function deleteEdfRecording(subjectId: number, edfArtifactId: number): Promise<DeleteEdfResult> {
  return apiDelete<DeleteEdfResult>(`/subjects/${subjectId}/edf/${edfArtifactId}`);
}

export interface RecordingAnnotation {
  onset: number;
  duration: number;
  description: string;
}

/** The saved ictal/interictal params this recording was last computed with,
 * plus its parsed EDF+ annotations -- null/empty rather than a 404 when
 * nothing has been computed yet or the file carries no annotations. */
export interface RecordingParams {
  edf_artifact_id: number;
  ictal_params: EiComputeParams | null;
  interictal_params: HfoComputeParams | null;
  annotations: RecordingAnnotation[];
  updated_at: string | null;
}

export function getRecordingParams(subjectId: number, edfArtifactId: number): Promise<RecordingParams> {
  return apiGet<RecordingParams>(`/subjects/${subjectId}/edf/${edfArtifactId}/params`);
}

export interface EdfWindowParams {
  start: number;
  end: number;
  channels?: string[];
  bandLow?: number;
  bandHigh?: number;
  /** Power-line frequency to notch: 50 Europe/Asia, 60 North America. */
  mainsFreq?: number;
  /** Clinical review time constant in seconds; 0 = low cut off. Present selects
   * review filtering (causal one-pole high-pass + `bandHigh` as an independent
   * high cut) over the `bandLow`/`bandHigh` analysis bandpass. */
  tc?: number;
  /** 'bipolar' makes `channels` name derivations (A1-A2); 'none' preserves raw contacts. */
  reference?: EdfDisplayReference;
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
  if (params.mainsFreq != null) qs.set("mains_freq", String(params.mainsFreq));
  if (params.tc != null) qs.set("tc", String(params.tc));
  if (params.reference != null) qs.set("reference", params.reference);
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
  /** Clamped server-side to just under Nyquist. */
  band_high?: number;
  /** Power-line frequency: 50 Europe/Asia, 60 North America. Defaults to 50. */
  mains_freq?: number;
  /** Channel names to keep; omitted means every channel in the file. */
  remain_chns?: string[];
  /** Re-reference montage. Bipolar (the server default) cancels the shared
   * reference and the volume-conducted far field; under it the analysed
   * channels are derivations (A1-A2), not contacts. */
  reference?: EiReference;
}

export type EiReference = "car" | "bipolar";
export type EdfDisplayReference = EiReference | "none";

export function computeEi(subjectId: number, edfArtifactId: number, params: EiComputeParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/ictal/${edfArtifactId}/ei`, params);
}

export interface BipolarPreview {
  n_contacts: number;
  n_pairs: number;
  pairs: string[];
  /** Contacts that end up in no derivation: unparseable, alone on a shaft, or
   * stranded next to a numbering gap. They get no EI score at all. */
  unpairable: string[];
  skipped_gaps: { shaft: string; between: [string, string] }[];
  /** remain_chns entries this recording doesn't have; the EI job rejects them. */
  unknown_channels: string[];
}

export function getBipolarPreview(
  subjectId: number,
  edfArtifactId: number,
  remainChns?: string[],
): Promise<BipolarPreview> {
  const qs = new URLSearchParams();
  for (const c of remainChns ?? []) qs.append("remain_chns", c);
  const suffix = qs.toString() ? `?${qs}` : "";
  return apiGet<BipolarPreview>(
    `/subjects/${subjectId}/ictal/${edfArtifactId}/bipolar-preview${suffix}`,
  );
}

// Mirrors v2/client/api_client.py's _RETRY_DISPATCH: every job type's
// params_json already carries everything its original POST body needed, so
// retrying is just re-issuing the same call with those same params. The
// hfo/soz entries POST to the server routes directly because the web client
// has no dedicated wrappers for them yet (Phase 4/5 features).
type RetryFn = (subjectId: number, p: Record<string, unknown>) => Promise<Job>;

const RETRY_DISPATCH: Record<string, RetryFn> = {
  recon: (subjectId, p) => runRecon(subjectId, (p.recon_type as ReconType) ?? "recon-all", p.age_months as number | undefined),
  ct_register: (subjectId) => registerCt(subjectId),
  elec_detect: (subjectId, p) => detectElectrodes(subjectId, p as unknown as DetectParams),
  elec_segment: (subjectId, p) => segmentElectrodes(subjectId, p as SegmentParams),
  elec_import: (subjectId, p) =>
    importContacts(subjectId, {
      contacts: p.contacts as ImportContactItem[] | undefined,
      csvText: p.csv_text as string | undefined,
    }),
  slicer_mrb_parse: (subjectId, p) => startSlicerMrbPreview(subjectId, p.mrb_artifact_id as number),
  ei_compute: (subjectId, p) => {
    const { edf_artifact_id, ...params } = p;
    return computeEi(subjectId, edf_artifact_id as number, params as unknown as EiComputeParams);
  },
  hfo_compute: (subjectId, p) => {
    const { edf_artifact_id, ...params } = p;
    return apiPost<Job>(`/subjects/${subjectId}/interictal/${edf_artifact_id}/hfo`, params);
  },
  soz_fuse: (subjectId, p) => fuseSoz(subjectId, p as SozFuseParams),
};

export function retryJob(job: Job): Promise<Job> {
  const dispatch = RETRY_DISPATCH[job.job_type];
  if (!dispatch) {
    return Promise.reject(new Error(`Don't know how to retry job type '${job.job_type}'`));
  }
  return dispatch(job.subject_id, job.params_json ?? {});
}

export interface EiResult {
  /** The analysed channels: derivations (A1-A2) under bipolar, contacts under CAR. */
  chn_names: string[];
  /** The same result projected back onto contacts, for anything that joins on
   * contact names. Equals chn_names under CAR. */
  contact_names: string[];
  ei_by_contact: (number | null)[];
  /** null where the channel had no usable baseline (dead electrode) -- an
   * undefined EI, distinct from a real score of 0 meaning "quiet". */
  ei: (number | null)[];
  ei_raw: (number | null)[];
  hfer: (number | null)[];
  time_coef: (number | null)[];
  /** How the result was computed and whether it can be trusted -- absent on
   * results written before diagnostics existed. */
  diagnostics?: {
    method?: "band_ratio" | "broadband";
    /** Absent on results computed before the montage was selectable -- those were CAR. */
    reference?: EiReference;
    n_channels?: number;
    n_crossed?: number;
    n_never_crossed?: number;
    frac_onset_at_window_start?: number;
    degenerate_window?: boolean;
    dead_channels?: string[];
    saturated_channels?: string[];
  };
  /** The job's own params -- the windows and bands this result was computed
   * over. Session state can't supply them after a reload. */
  params: {
    baseline_start: number;
    baseline_end: number;
    target_start: number;
    target_end: number;
    band_low?: number;
    band_high?: number;
    mains_freq?: number;
    ei_method?: "band_ratio" | "broadband";
    reference?: EiReference;
  };
}

export function getEiResult(subjectId: number, edfArtifactId: number): Promise<EiResult> {
  return apiGet<EiResult>(`/subjects/${subjectId}/ictal/${edfArtifactId}/ei-result`);
}

export interface HfoComputeParams {
  band_low?: number;
  /** Clamped server-side to just under Nyquist. */
  band_high?: number;
  rel_thresh?: number;
  abs_thresh?: number;
  min_gap?: number;
  min_last?: number;
  remain_chns?: string[];
  /** Power-line frequency: 50 Europe/Asia, 60 North America. Defaults to 50.
   * Matters more here than for EI -- on 60Hz mains the 180 and 240Hz harmonics
   * fall inside the 80-250Hz ripple band and get detected as HFOs. */
  mains_freq?: number;
  /** Analysis window in seconds; omit for the whole recording. */
  start_time?: number | null;
  end_time?: number | null;
}

export function computeHfo(subjectId: number, edfArtifactId: number, params: HfoComputeParams): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/interictal/${edfArtifactId}/hfo`, params);
}

export interface HfoResult {
  chn_names: string[];
  event_counts: number[];
  /** event_times[i] is channel chn_names[i]'s list of [startSec, endSec] events. */
  event_times: [number, number][][];
}

export function getHfoResult(subjectId: number, edfArtifactId: number): Promise<HfoResult> {
  return apiGet<HfoResult>(`/subjects/${subjectId}/interictal/${edfArtifactId}/hfo-result`);
}

export interface SozFuseParams {
  ei_artifact_id?: number;
  hi_artifact_id?: number;
}

export function fuseSoz(subjectId: number, params: SozFuseParams = {}): Promise<Job> {
  return apiPost<Job>(`/subjects/${subjectId}/soz/fuse`, params);
}

export interface SozResultRow {
  contact: string;
  x: number;
  y: number;
  z: number;
  /** null when this contact is absent from the EI/HI results (see server's
   * load_result_rows NaN->null sanitization). */
  ei: number | null;
  hi: number | null;
  ei_percentile: number | null;
  hi_percentile: number | null;
  combined_score: number;
  suspect_ei: boolean;
  suspect_hi: boolean;
}

export function getSozResult(subjectId: number): Promise<SozResultRow[]> {
  return apiGet<SozResultRow[]>(`/subjects/${subjectId}/soz/result`);
}
