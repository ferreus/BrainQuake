import type { Dispatch } from "react";
import { Button, Divider, Group, SegmentedControl, Select, Switch, Text } from "@mantine/core";
import {
  HIGH_CUT_PRESETS,
  PAGE_SECONDS_PRESETS,
  SENSITIVITY_PRESETS,
  TIME_CONSTANT_PRESETS,
  type ClinicalMontage,
  type ClinicalViewAction,
  type ClinicalViewState,
} from "./useClinicalViewState";

interface ClinicalToolbarProps {
  state: ClinicalViewState;
  dispatch: Dispatch<ClinicalViewAction>;
}

const OFF = "off";

/** Corner frequency of a time constant, the way an NK dropdown labels it. */
const tcLabel = (tc: number) => `${tc} s (${(1 / (2 * Math.PI * tc)).toFixed(2)} Hz)`;

/** The Nihon Kohden review controls: Sens, TC, high cut, page length, montage.
 * All presets, so no invalid state is reachable and nothing needs clamping. */
export function ClinicalToolbar({ state, dispatch }: ClinicalToolbarProps) {
  return (
    <Group gap="md" wrap="wrap" align="center">
      <Group gap={4}>
        <Text size="xs" c="dimmed">Channels</Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAGE_CHANNELS", direction: -1 })}>
          Up
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAGE_CHANNELS", direction: 1 })}>
          Down
        </Button>
        <Select
          size="xs"
          w={70}
          aria-label="Channels per page"
          allowDeselect={false}
          value={String(state.dispChansNum)}
          data={[10, 15, 20, 25, 32].map((n) => ({ value: String(n), label: String(n) }))}
          onChange={(v) => v && dispatch({ type: "SET_CHANS_NUM", value: Number(v) })}
        />
      </Group>
      <Divider orientation="vertical" />
      <Group gap={4}>
        <Text size="xs" c="dimmed">Sens</Text>
        <Select
          size="xs"
          w={104}
          aria-label="Sensitivity"
          allowDeselect={false}
          value={String(state.sensitivity)}
          data={SENSITIVITY_PRESETS.map((v) => ({ value: String(v), label: `${v} µV/mm` }))}
          onChange={(v) => v && dispatch({ type: "SET_SENSITIVITY", value: Number(v) })}
        />
      </Group>
      <Divider orientation="vertical" />
      <Group gap={4}>
        <Text size="xs" c="dimmed">TC</Text>
        <Select
          size="xs"
          w={128}
          aria-label="Time constant"
          allowDeselect={false}
          value={state.timeConstant == null ? OFF : String(state.timeConstant)}
          data={[
            { value: OFF, label: "off" },
            ...TIME_CONSTANT_PRESETS.map((v) => ({ value: String(v), label: tcLabel(v) })),
          ]}
          onChange={(v) => v && dispatch({ type: "SET_TIME_CONSTANT", value: v === OFF ? null : Number(v) })}
        />
        <Text size="xs" c="dimmed">HC</Text>
        <Select
          size="xs"
          w={86}
          aria-label="High cut"
          allowDeselect={false}
          value={state.highCut == null ? OFF : String(state.highCut)}
          data={[
            { value: OFF, label: "off" },
            ...HIGH_CUT_PRESETS.map((v) => ({ value: String(v), label: `${v} Hz` })),
          ]}
          onChange={(v) => v && dispatch({ type: "SET_HIGH_CUT", value: v === OFF ? null : Number(v) })}
        />
      </Group>
      <Divider orientation="vertical" />
      <Group gap={4}>
        <Text size="xs" c="dimmed">Time</Text>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAN_TIME", direction: -1 })}>
          Left
        </Button>
        <Button size="xs" variant="default" onClick={() => dispatch({ type: "PAN_TIME", direction: 1 })}>
          Right
        </Button>
        <Select
          size="xs"
          w={78}
          aria-label="Page length"
          allowDeselect={false}
          value={String(state.pageSeconds)}
          data={PAGE_SECONDS_PRESETS.map((v) => ({ value: String(v), label: `${v} s` }))}
          onChange={(v) => v && dispatch({ type: "SET_PAGE_SECONDS", value: Number(v) })}
        />
      </Group>
      <Divider orientation="vertical" />
      <SegmentedControl
        size="xs"
        value={state.montage}
        onChange={(v) => dispatch({ type: "SET_MONTAGE", value: v as ClinicalMontage })}
        data={[
          { label: "Referential", value: "none" },
          { label: "CAR", value: "car" },
          { label: "Bipolar", value: "bipolar" },
        ]}
      />
      <Divider orientation="vertical" />
      {/* The wrong mains value notches clean signal out of the traces being
          reviewed and leaves the real interference in. */}
      <SegmentedControl
        size="xs"
        value={String(state.mainsFreq)}
        onChange={(v) => dispatch({ type: "SET_MAINS_FREQ", value: Number(v) })}
        data={[
          { label: "50Hz", value: "50" },
          { label: "60Hz", value: "60" },
        ]}
      />
      <Switch
        size="xs"
        checked={state.negativeUp}
        onChange={() => dispatch({ type: "TOGGLE_POLARITY" })}
        label="Negative up"
      />
    </Group>
  );
}
