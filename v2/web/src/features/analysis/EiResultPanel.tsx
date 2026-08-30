import { useMemo, useState } from "react";
import { Badge, Group, Paper, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useEiResult } from "../../api/queries/useIctal";
import { RankedChannelChart } from "./RankedChannelChart";
import { SpectrogramModal } from "./SpectrogramModal";

interface EiResultPanelProps {
  subjectId: number;
  edfArtifactId: number;
}

// Validated pair (see dataviz skill's validator): categorical blue for normal
// bars, "critical" status red for above-threshold bars -- distinct from each
// other and from the categorical series slots, in both color schemes.
const COLORS = {
  light: { bar: "#2a78d6", flagged: "#d03b3b", axis: "#52514e", text: "#0b0b0b" },
  dark: { bar: "#3987e5", flagged: "#e66767", axis: "#c3c2b7", text: "#ffffff" },
};

/** Per-channel EI bar chart with a mean+std threshold line, mirroring
 * client_ictal.py's ei_plot_xw_func. Clicking a bar opens the per-channel
 * raw-signal + spectrogram drill-down (ei_press_func). Threshold is fixed
 * (mean+std) rather than the legacy's interactive right-click-drag -- a
 * disclosed simplification for this phase. */
export function EiResultPanel({ subjectId, edfArtifactId }: EiResultPanelProps) {
  const { data, isLoading, isError } = useEiResult(subjectId, edfArtifactId, true);
  const scheme = useComputedColorScheme("light");
  const [drillDownChannel, setDrillDownChannel] = useState<string | null>(null);

  // The window this result was computed over, from the job itself -- taking it
  // from the live baseline/target selection instead made the drill-down dead
  // after a reload or a switch to another recording.
  const params = data?.params;
  const targetRange: [number, number] | null = params
    ? [params.target_start, params.target_end]
    : null;

  const stats = useMemo(() => {
    if (!data || data.ei.length === 0) return null;
    // Dead channels come back as null; averaging them in would drag the
    // threshold toward zero and mark half the montage as suspect.
    const vals = data.ei.filter((v): v is number => v != null && Number.isFinite(v));
    if (vals.length === 0) return null;
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    const variance = vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length;
    return { mean, threshold: mean + Math.sqrt(variance) };
  }, [data]);

  if (isLoading) return null;
  if (isError || !data || !stats) {
    return (
      <Paper withBorder p="sm" style={{ flex: 1 }}>
        <Title order={6} mb="xs">
          Epileptogenicity Index (EI)
        </Title>
        <Text size="xs" c="dimmed">
          Not computed yet for this recording.
        </Text>
      </Paper>
    );
  }

  // Absent on results computed before the montage was selectable -- those were CAR.
  const reference = data.diagnostics?.reference;
  const referenceLabel =
    reference === "bipolar" ? "Bipolar" : reference === "car" ? "Common average" : "Common average (legacy)";

  return (
    <Paper withBorder p="sm" style={{ flex: 1, minWidth: 0 }}>
      <Group gap="xs" mb="xs" wrap="nowrap">
        <Title order={6}>Epileptogenicity Index (EI)</Title>
        <Badge size="xs" variant="light" color={reference === "bipolar" ? "blue" : "gray"}>
          {referenceLabel}
        </Badge>
      </Group>
      <RankedChannelChart
        names={data.chn_names}
        values={data.ei}
        colors={COLORS[scheme]}
        ariaLabel="EI per channel bar chart"
        threshold={stats.threshold}
        thresholdLabel="mean + std"
        onBarClick={targetRange ? setDrillDownChannel : undefined}
      />
      <Text size="xs" c="dimmed" mt={4}>
        Click a {reference === "bipolar" ? "derivation" : "channel"} to view its raw signal
        + spectrogram.
      </Text>

      <SpectrogramModal
        subjectId={subjectId}
        edfArtifactId={edfArtifactId}
        channel={drillDownChannel}
        range={targetRange}
        bandLow={params?.band_low ?? 1}
        bandHigh={params?.band_high ?? 300}
        reference={reference ?? "car"}
        onClose={() => setDrillDownChannel(null)}
      />
    </Paper>
  );
}
