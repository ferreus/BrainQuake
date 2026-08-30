import type { ComponentType, Dispatch } from "react";
import type { EdfMeta, RecordingParams } from "../../api/endpoints";
import type { BaselineTargetSelection } from "../../components/eeg/BaselineTargetLayer";
import type { EegViewerAction, EegViewerState } from "../../components/eeg/useEegViewerState";
import {
  EiParams, EiResult, FragilityParams, FragilityResult, HfoParams, HfoResult,
} from "./processAdapters";

export interface ProcessPaneProps {
  subjectId: number;
  edfArtifactId: number;
  meta: EdfMeta;
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
  remainChannels: string[];
  recordingParams?: RecordingParams;
  /** Range-slot picker, shared with the canvas marker layer. */
  selection: BaselineTargetSelection;
}

/** What a process needs picked on each recording it runs over. The run-set
 * table renders one column per slot; `instant` slots with `fromAnnotation` are
 * filled from the recording's own EDF+ marks. */
export type InputSlot =
  | { key: string; type: "instant"; label: string; fromAnnotation?: boolean; optional?: boolean }
  | { key: string; type: "range"; label: string; optional?: boolean };

/** What a process produces. Drives the result pane and the canvas overlays, so
 * a new method gets both by declaring a kind rather than by adding a branch. */
export type OutputSpec =
  | { kind: "channel_scores"; label: string }
  | { kind: "channel_time"; label: string }
  | { kind: "events"; label: string };

export interface AnalysisProcess {
  id: string;
  label: string;
  resultTabLabel: string;
  /** Header of the collapsible compute section in the sidebar. */
  computeTitle: string;
  inputs: InputSlot[];
  outputs: OutputSpec[];
  /** Seeds the trace display filter. Replaces useEegViewerState's old
   * mode: "ictal" | "interictal" branch -- same two bands, declared instead. */
  viewerDefaults: { filterBandLow: number; filterBandHigh: number };
  ParamsForm: ComponentType<ProcessPaneProps>;
  ResultView: ComponentType<ProcessPaneProps>;
}

export const ANALYSIS_PROCESSES: AnalysisProcess[] = [
  {
    id: "ei",
    label: "Epileptogenicity Index",
    resultTabLabel: "EI Result",
    computeTitle: "Compute EI",
    inputs: [
      { key: "baseline", type: "range", label: "Baseline" },
      { key: "target", type: "range", label: "Target" },
    ],
    outputs: [
      { kind: "channel_scores", label: "EI" },
      { kind: "channel_time", label: "HFER" },
    ],
    viewerDefaults: { filterBandLow: 60, filterBandHigh: 140 },
    ParamsForm: EiParams,
    ResultView: EiResult,
  },
  {
    id: "hfo",
    label: "High-frequency oscillations",
    resultTabLabel: "HFO Result",
    computeTitle: "Compute HFO (High-frequency events)",
    inputs: [{ key: "window", type: "range", label: "Window", optional: true }],
    outputs: [
      { kind: "channel_scores", label: "Event count" },
      { kind: "events", label: "Detections" },
    ],
    viewerDefaults: { filterBandLow: 80, filterBandHigh: 250 },
    ParamsForm: HfoParams,
    ResultView: HfoResult,
  },
  {
    id: "fragility",
    label: "Neural fragility",
    resultTabLabel: "Fragility Result",
    computeTitle: "Compute fragility",
    // Runs over a set of seizures; the onset comes from each recording's own
    // EDF+ marks, which the server extracted at upload.
    inputs: [{ key: "onset_s", type: "instant", label: "Seizure onset", fromAnnotation: true }],
    outputs: [
      { kind: "channel_scores", label: "Fragility" },
      { kind: "channel_time", label: "Fragility over time" },
    ],
    // Li et al. fit the broadband signal; the display band is only what the
    // reviewer sees while checking the onset mark.
    viewerDefaults: { filterBandLow: 1, filterBandHigh: 300 },
    ParamsForm: FragilityParams,
    ResultView: FragilityResult,
  },
];

export const DEFAULT_PROCESS_ID = "ei";

export function findProcess(id: string): AnalysisProcess {
  return ANALYSIS_PROCESSES.find((p) => p.id === id) ?? ANALYSIS_PROCESSES[0];
}

export function processProduces(process: AnalysisProcess, kind: OutputSpec["kind"]): boolean {
  return process.outputs.some((o) => o.kind === kind);
}
