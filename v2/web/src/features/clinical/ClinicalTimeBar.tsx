import { useState, type Dispatch } from "react";
import { Divider, Group, Paper, Slider, Stack, Text, TextInput } from "@mantine/core";
import { formatClock, formatElapsed, parseTimeInput } from "../../lib/eegTime";
import type { ClinicalViewAction, ClinicalViewState } from "./useClinicalViewState";

interface ClinicalTimeBarProps {
  state: ClinicalViewState;
  dispatch: Dispatch<ClinicalViewAction>;
  /** Seconds since midnight of the recording start, or null when the EDF
   * header carried no date -- then only elapsed time is meaningful. */
  clockOrigin: number | null;
  /** Elapsed seconds under the pointer, or null when it is off the canvas. */
  cursorTime: number | null;
}

/** Reads the time under the pointer when there is one, the page start
 * otherwise. */
function Readout({ label, value }: { label: string; value: string }) {
  return (
    <Stack gap={0}>
      <Text size="10px" c="dimmed" tt="uppercase" lh={1.2}>{label}</Text>
      <Text size="xl" ff="monospace" fw={600} lh={1.2}>{value}</Text>
    </Stack>
  );
}

/** The "where am I" strip: clock time and elapsed time for the page on screen,
 * tracking the pointer while it is over the traces, plus a jump box and a
 * scrubber over the whole recording. */
export function ClinicalTimeBar({ state, dispatch, clockOrigin, cursorTime }: ClinicalTimeBarProps) {
  const [jump, setJump] = useState("");
  const [jumpError, setJumpError] = useState(false);

  const maxStart = Math.max(0, state.durationSec - state.pageSeconds);
  const shown = cursorTime ?? state.timeStart;
  const tracking = cursorTime != null;
  const clockOf = (t: number) => (clockOrigin == null ? "—" : formatClock(clockOrigin + t));

  function submitJump() {
    const t = parseTimeInput(jump, clockOrigin);
    if (t == null) {
      setJumpError(true);
      return;
    }
    setJumpError(false);
    dispatch({ type: "SET_TIME_START", value: t });
  }

  return (
    <Paper withBorder p="xs" radius="sm">
      <Group gap="lg" wrap="wrap" align="center">
        <Readout label={tracking ? "Clock · cursor" : "Clock · page start"} value={clockOf(shown)} />
        <Readout
          label={tracking ? "Elapsed · cursor" : "Elapsed · page start"}
          value={`${formatElapsed(shown)} / ${formatElapsed(state.durationSec)}`}
        />
        <Divider orientation="vertical" />
        <Text size="xs" c="dimmed">
          Window {clockOf(state.timeStart)} → {clockOf(state.timeStart + state.pageSeconds)} · {state.pageSeconds} s page
        </Text>
        <Divider orientation="vertical" />
        <TextInput
          size="xs"
          w={130}
          aria-label="Jump to time"
          placeholder={clockOrigin == null ? "seconds" : "HH:MM:SS"}
          value={jump}
          error={jumpError}
          onChange={(e) => {
            setJump(e.currentTarget.value);
            setJumpError(false);
          }}
          onKeyDown={(e) => e.key === "Enter" && submitJump()}
          onBlur={() => jump.trim() && submitJump()}
        />
        <Slider
          style={{ flex: 1, minWidth: 200 }}
          size="sm"
          label={null}
          disabled={maxStart === 0}
          min={0}
          max={maxStart || 1}
          step={Math.min(1, state.pageSeconds)}
          value={Math.min(state.timeStart, maxStart)}
          onChange={(v) => dispatch({ type: "SET_TIME_START", value: v })}
        />
      </Group>
    </Paper>
  );
}
