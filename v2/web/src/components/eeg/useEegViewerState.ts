import { useReducer } from "react";
import type { Dispatch } from "react";

export type EegMode = "ictal" | "interictal";

export interface EegViewerState {
  dispChansNum: number;
  dispChansStart: number;
  dispWaveMul: number;
  dispTimeWin: number;
  dispTimeStart: number;
  excludedChannels: Set<string>;
  filterBandLow: number;
  filterBandHigh: number;
  filterEnabled: boolean;
}

export type EegViewerAction =
  | { type: "PAGE_CHANNELS"; direction: 1 | -1 }
  | { type: "SET_CHANS_NUM"; value: number }
  | { type: "SET_GAIN"; multiplier: number }
  | { type: "PAN_TIME"; direction: 1 | -1 }
  | { type: "SET_TIME_WIN"; delta: number }
  | { type: "SET_TIME_START"; value: number }
  | { type: "DELETE_CHANNELS"; channels: string[] }
  | { type: "SET_FILTER_BAND"; low: number; high: number }
  | { type: "TOGGLE_FILTER" };

function reducer(state: EegViewerState, action: EegViewerAction): EegViewerState {
  switch (action.type) {
    case "PAGE_CHANNELS": {
      const next = state.dispChansStart + action.direction * state.dispChansNum;
      return { ...state, dispChansStart: Math.max(0, next) };
    }
    case "SET_CHANS_NUM":
      return { ...state, dispChansNum: Math.max(1, action.value) };
    case "SET_GAIN":
      return { ...state, dispWaveMul: Math.max(0.1, state.dispWaveMul * action.multiplier) };
    case "PAN_TIME": {
      const delta = action.direction * state.dispTimeWin * 0.2;
      return { ...state, dispTimeStart: Math.max(0, state.dispTimeStart + delta) };
    }
    case "SET_TIME_WIN":
      return { ...state, dispTimeWin: Math.max(2, state.dispTimeWin + action.delta) };
    case "SET_TIME_START":
      return { ...state, dispTimeStart: Math.max(0, action.value) };
    case "DELETE_CHANNELS": {
      const next = new Set(state.excludedChannels);
      action.channels.forEach((c) => next.add(c));
      return { ...state, excludedChannels: next };
    }
    case "SET_FILTER_BAND":
      return { ...state, filterBandLow: action.low, filterBandHigh: action.high };
    case "TOGGLE_FILTER":
      return { ...state, filterEnabled: !state.filterEnabled };
    default:
      return state;
  }
}

/**
 * Shared pan/zoom/gain/channel-window/filter state for the EEG canvas --
 * directly reproduces the legacy client_ictal.py/client_inter.py interaction
 * model (disp_chans_num, disp_wave_mul, disp_time_win, etc.), which was
 * near-identical duplicated code in both files; this is the single copy.
 * Mode only changes the initial filter band default (60-140Hz ictal vs
 * 80-250Hz interictal, matching the legacy tabs' own defaults).
 */
export function useEegViewerState(mode: EegMode): {
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
} {
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    dispChansNum: 20,
    dispChansStart: 0,
    dispWaveMul: 10,
    dispTimeWin: 5,
    dispTimeStart: 0,
    excludedChannels: new Set<string>(),
    filterBandLow: mode === "ictal" ? 60 : 80,
    filterBandHigh: mode === "ictal" ? 140 : 250,
    filterEnabled: true,
  }));
  return { state, dispatch };
}
