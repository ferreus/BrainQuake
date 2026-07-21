import type { Dispatch } from "react";
import { Button, Group, Stack, Switch, Text } from "@mantine/core";
import type { EegViewerAction, EegViewerState } from "./useEegViewerState";

interface EegToolbarProps {
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
}

/** Pan/zoom/gain/channel-window controls, reproducing the legacy "Adjust
 * signal" groupbox: win up/down (page channels), chans+/-, wave+/-,
 * left/right (pan time), shrink/expand (time window). */
export function EegToolbar({ state, dispatch }: EegToolbarProps) {
  return (
    <Stack gap={6}>
      <Group gap={4}>
        <Text size="xs" w={70}>
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
      <Group gap={4}>
        <Text size="xs" w={70}>
          Gain
        </Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_GAIN", multiplier: 1.5 })}>
          Wave+
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "SET_GAIN", multiplier: 0.75 })}>
          Wave-
        </Button>
      </Group>
      <Group gap={4}>
        <Text size="xs" w={70}>
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
      <Switch
        size="xs"
        checked={state.filterEnabled}
        onChange={() => dispatch({ type: "TOGGLE_FILTER" })}
        label={`Filter ${state.filterBandLow}-${state.filterBandHigh}Hz`}
      />
    </Stack>
  );
}
