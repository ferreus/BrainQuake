import { useCallback, useState } from "react";

type ClickStage = "baseline-start" | "baseline-end" | "target-start" | "target-end" | null;

export interface BaselineTargetSelection {
  baselineRange: [number, number] | null;
  targetRange: [number, number] | null;
  awaitingClick: ClickStage;
  startBaselineSelect: () => void;
  startTargetSelect: () => void;
  handleClick: (time: number) => void;
  reset: () => void;
}

/**
 * Two-click baseline/target window selector, mirroring client_ictal.py's
 * choose_baseline/choose_target + canvas_press_button: arm "Set Baseline" (or
 * "Set Target"), then click twice on the EEG trace to mark the window's start
 * and end. Session-only, like the legacy GUI state -- not persisted.
 */
export function useBaselineTargetSelection(): BaselineTargetSelection {
  const [baselineRange, setBaselineRange] = useState<[number, number] | null>(null);
  const [targetRange, setTargetRange] = useState<[number, number] | null>(null);
  const [awaitingClick, setAwaitingClick] = useState<ClickStage>(null);
  const [pendingStart, setPendingStart] = useState<number | null>(null);

  const startBaselineSelect = useCallback(() => {
    setAwaitingClick("baseline-start");
    setPendingStart(null);
  }, []);

  const startTargetSelect = useCallback(() => {
    setAwaitingClick("target-start");
    setPendingStart(null);
  }, []);

  const handleClick = useCallback(
    (time: number) => {
      if (awaitingClick === "baseline-start") {
        setPendingStart(time);
        setAwaitingClick("baseline-end");
      } else if (awaitingClick === "baseline-end" && pendingStart != null) {
        setBaselineRange(pendingStart < time ? [pendingStart, time] : [time, pendingStart]);
        setAwaitingClick(null);
        setPendingStart(null);
      } else if (awaitingClick === "target-start") {
        setPendingStart(time);
        setAwaitingClick("target-end");
      } else if (awaitingClick === "target-end" && pendingStart != null) {
        setTargetRange(pendingStart < time ? [pendingStart, time] : [time, pendingStart]);
        setAwaitingClick(null);
        setPendingStart(null);
      }
    },
    [awaitingClick, pendingStart],
  );

  const reset = useCallback(() => {
    setBaselineRange(null);
    setTargetRange(null);
    setAwaitingClick(null);
    setPendingStart(null);
  }, []);

  return { baselineRange, targetRange, awaitingClick, startBaselineSelect, startTargetSelect, handleClick, reset };
}
