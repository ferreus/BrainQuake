import type { Dispatch } from "react";
import { Button, Divider, Group, Switch, Text } from "@mantine/core";
import type { EegViewerAction, EegViewerState } from "./useEegViewerState";

interface EegToolbarProps {
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
}

/** Pan/zoom/gain/channel-window controls, reproducing the legacy "Adjust
 * signal" groupbox: win up/down (page channels), chans+/-, wave+/-,
 * left/right (pan time), shrink/expand (time window). Single horizontal row
 * (wraps on narrow viewports) so it can sit inline with the EDF picker
 * instead of eating a whole sidebar column. */
export function EegToolbar({ state, dispatch }: EegToolbarProps) {
  return (
    <Group gap="md" wrap="wrap" align="center">
      <Group gap={4}>
        <Text size="xs" c="dimmed">
          Channels
        </Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAGE_CHANNELS", direction: -1 })}>
          Up
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAGE_CHANNELS", direction: 1 })}>
          Down
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_CHANS_NUM", value: state.dispChansNum * 2 })}>
          Chans+
        </Button>
        <Button
          size="xs"
          variant="default"
          onClick={() => dispatch({ type: "SET_CHANS_NUM", value: Math.max(1, Math.floor(state.dispChansNum / 2)) })}
        >
          Chans-
        </Button>
      </Group>
      <Divider orientation="vertical" />
      <Group gap={4}>
        <Text size="xs" c="dimmed">
          Gain
        </Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_GAIN", multiplier: 1.5 })}>
          Wave+
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_GAIN", multiplier: 0.75 })}>
          Wave-
        </Button>
      </Group>
      <Divider orientation="vertical" />
      <Group gap={4}>
        <Text size="xs" c="dimmed">
          Time
        </Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAN_TIME", direction: -1 })}>
          Left
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAN_TIME", direction: 1 })}>
          Right
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_TIME_WIN", delta: -2 })}>
          Shrink
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_TIME_WIN", delta: 2 })}>
          Expand
        </Button>
      </Group>
      <Divider orientation="vertical" />
      <Switch
        size="xs"
        checked={state.filterEnabled}
        onChange={() => dispatch({ type: "TOGGLE_FILTER" })}
        label={`Filter ${state.filterBandLow}-${state.filterBandHigh}Hz`}
      />
    </Group>
  );
}
