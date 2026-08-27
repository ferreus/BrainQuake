import { useReducer } from "react";
import type { Dispatch } from "react";

/** How the traces are re-referenced before display. Referential ("none") is
 * what a Nihon Kohden review screen shows by default. Under "bipolar" the rows
 * are derivations (A1-A2), not contacts. */
export type ClinicalMontage = "none" | "car" | "bipolar";

/** Nihon Kohden dropdown values. Presets rather than free entry: every reachable
 * state is valid, so nothing here needs clamping or an error state. */
export const SENSITIVITY_PRESETS = [2, 5, 10, 20, 50, 75, 100, 150, 200, 300];
export const TIME_CONSTANT_PRESETS = [0.003, 0.01, 0.03, 0.1, 0.3, 1, 2];
export const HIGH_CUT_PRESETS = [15, 30, 60, 70, 120, 300];
export const PAGE_SECONDS_PRESETS = [5, 10, 15, 20, 30];

export interface ClinicalViewState {
  dispChansNum: number;
  dispChansStart: number;
  /** uV per millimetre, as on the NK Sens dropdown. One trace row is ROW_MM
   * tall, so a row spans sensitivity * ROW_MM uV. */
  sensitivity: number;
  pageSeconds: number;
  timeStart: number;
  /** Recording length, from the EDF meta. Held here so timeStart has an upper
   * bound to clamp against -- panning, the scrubber and the jump box all go
   * through the same one. 0 until a recording loads. */
  durationSec: number;
  /** Contacts on screen. Display only -- this view feeds no computation, which
   * is the whole reason it is separate from the ictal/interictal viewer. */
  selectedChannels: Set<string>;
  loadedEdfId: number | null;
  montage: ClinicalMontage;
  /** Seconds. null = low cut off. */
  timeConstant: number | null;
  /** Hz. null = high cut off. */
  highCut: number | null;
  mainsFreq: number;
  /** Clinical EEG convention: negative deflections point up. */
  negativeUp: boolean;
}

export type ClinicalViewAction =
  | { type: "PAGE_CHANNELS"; direction: 1 | -1 }
  | { type: "SET_CHANS_NUM"; value: number }
  | { type: "SET_SENSITIVITY"; value: number }
  | { type: "PAN_TIME"; direction: 1 | -1 }
  | { type: "SET_PAGE_SECONDS"; value: number }
  | { type: "SET_TIME_START"; value: number }
  | { type: "SET_SELECTED_CHANNELS"; channels: string[] }
  | { type: "SET_MONTAGE"; value: ClinicalMontage }
  | { type: "SET_TIME_CONSTANT"; value: number | null }
  | { type: "SET_HIGH_CUT"; value: number | null }
  | { type: "SET_MAINS_FREQ"; value: number }
  | { type: "TOGGLE_POLARITY" }
  | {
      type: "LOAD_RECORDING";
      edfArtifactId: number;
      channels: string[];
      auxChannels: string[];
      durationSec: number;
    };

/** Keeps the window inside the recording. A page longer than the recording
 * pins to 0 rather than going negative. */
function clampStart(t: number, durationSec: number, pageSeconds: number): number {
  return Math.min(Math.max(0, durationSec - pageSeconds), Math.max(0, t));
}

function reducer(state: ClinicalViewState, action: ClinicalViewAction): ClinicalViewState {
  switch (action.type) {
    case "PAGE_CHANNELS":
      return { ...state, dispChansStart: Math.max(0, state.dispChansStart + action.direction * state.dispChansNum) };
    case "SET_CHANS_NUM":
      return { ...state, dispChansNum: Math.max(1, action.value) };
    case "SET_SENSITIVITY":
      return { ...state, sensitivity: action.value };
    case "PAN_TIME": {
      const t = state.timeStart + action.direction * state.pageSeconds * 0.2;
      return { ...state, timeStart: clampStart(t, state.durationSec, state.pageSeconds) };
    }
    case "SET_PAGE_SECONDS":
      // A longer page at the end of the recording would otherwise hang past it.
      return {
        ...state,
        pageSeconds: action.value,
        timeStart: clampStart(state.timeStart, state.durationSec, action.value),
      };
    case "SET_TIME_START":
      return { ...state, timeStart: clampStart(action.value, state.durationSec, state.pageSeconds) };
    case "SET_SELECTED_CHANNELS":
      // Deliberately does not reset dispChansStart: this fires on every
      // checkbox, and throwing away the scroll position on each one is hostile.
      return { ...state, selectedChannels: new Set(action.channels) };
    case "SET_MONTAGE":
      // Row identity changes (contacts vs derivations), so the page index no
      // longer refers to the same traces.
      return { ...state, montage: action.value, dispChansStart: 0 };
    case "SET_TIME_CONSTANT":
      return { ...state, timeConstant: action.value };
    case "SET_HIGH_CUT":
      return { ...state, highCut: action.value };
    case "SET_MAINS_FREQ":
      return { ...state, mainsFreq: action.value };
    case "TOGGLE_POLARITY":
      return { ...state, negativeUp: !state.negativeUp };
    case "LOAD_RECORDING": {
      // Once per recording. A scroll position valid in the previous file can be
      // past the end of this one, and its channel names do not apply here.
      if (state.loadedEdfId === action.edfArtifactId) return state;
      const aux = new Set(action.auxChannels);
      return {
        ...state,
        loadedEdfId: action.edfArtifactId,
        selectedChannels: new Set(action.channels.filter((c) => !aux.has(c))),
        durationSec: action.durationSec,
        timeStart: 0,
        dispChansStart: 0,
      };
    }
    default:
      return state;
  }
}

/** Pan/sensitivity/filter/montage state for the clinical EEG view.
 *
 * Separate from useEegViewerState by design: that one's channel list and filter
 * band feed the EI and HFO jobs, so reworking it for clinical review changed
 * what those jobs computed on. Nothing here reaches a computation.
 *
 * Defaults reproduce v2/tools/show_edf.py's example invocation
 * (75 uV/mm, TC 0.1 s, 70 Hz high cut, 10 s page).
 */
export function useClinicalViewState(): {
  state: ClinicalViewState;
  dispatch: Dispatch<ClinicalViewAction>;
} {
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    dispChansNum: 20,
    dispChansStart: 0,
    sensitivity: 75,
    pageSeconds: 10,
    timeStart: 0,
    durationSec: 0,
    selectedChannels: new Set<string>(),
    loadedEdfId: null,
    montage: "none" as ClinicalMontage,
    timeConstant: 0.1,
    highCut: 70,
    mainsFreq: 50,
    negativeUp: true,
  }));
  return { state, dispatch };
}
