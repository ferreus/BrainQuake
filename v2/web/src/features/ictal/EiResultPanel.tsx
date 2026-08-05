import { useMemo, useState } from "react";
import { Paper, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useEiResult } from "../../api/queries/useIctal";
import { ChannelBarChart } from "../../components/charts/ChannelBarChart";
import { SpectrogramModal } from "./SpectrogramModal";

interface EiResultPanelProps {
  subjectId: number;
  edfArtifactId: number;
  targetRange: [number, number] | null;
  bandLow: number;
  bandHigh: number;
}

// Validated pair (see dataviz skill's validator): categorical blue for normal
// bars, "critical" status red for above-threshold bars -- distinct from each
// other and from the categorical series slots, in both color schemes.
const COLORS = {
  light: { bar: "#2a78d6", flagged: "#d03b3b", threshold: "#52514e", text: "#0b0b0b" },
  dark: { bar: "#3987e5", flagged: "#e66767", threshold: "#c3c2b7", text: "#ffffff" },
};

/** Per-channel EI bar chart with a mean+std threshold line, mirroring
 * client_ictal.py's ei_plot_xw_func. Clicking a bar opens the per-channel
 * raw-signal + spectrogram drill-down (ei_press_func). Threshold is fixed
 * (mean+std) rather than the legacy's interactive right-click-drag -- a
 * disclosed simplification for this phase. */
export function EiResultPanel({ subjectId, edfArtifactId, targetRange, bandLow, bandHigh }: EiResultPanelProps) {
  const { data, isLoading, isError } = useEiResult(subjectId, edfArtifactId, true);
  const scheme = useComputedColorScheme("light");
  const colors = COLORS[scheme];
  const [drillDownChannel, setDrillDownChannel] = useState<string | null>(null);

  const stats = useMemo(() => {
    if (!data || data.ei.length === 0) return null;
    const mean = data.ei.reduce((a, b) => a + b, 0) / data.ei.length;
    const variance = data.ei.reduce((a, b) => a + (b - mean) ** 2, 0) / data.ei.length;
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

  return (
    <Paper withBorder p="sm" style={{ flex: 1, minWidth: 0 }}>
      <Title order={6} mb="xs">
        Epileptogenicity Index (EI)
      </Title>
      <ChannelBarChart
        channels={data.chn_names}
        values={data.ei}
        barColor={(v) => (v > stats.threshold ? colors.flagged : colors.bar)}
        axisColor={colors.threshold}
        labelColor={colors.text}
        showLabel={(v) => v > stats.threshold}
        threshold={{ value: stats.threshold, label: "mean + std", color: colors.threshold }}
        onBarClick={targetRange ? (name) => setDrillDownChannel(name) : undefined}
        headroom={1.1}
        ariaLabel="EI per channel bar chart"
      />
      <Text size="xs" c="dimmed" mt={4}>
        {targetRange
          ? "Click a bar to view that channel's raw signal + spectrogram."
          : "Set a target range again this session to enable per-channel drill-down."}
      </Text>

      <SpectrogramModal
        subjectId={subjectId}
        edfArtifactId={edfArtifactId}
        channel={drillDownChannel}
        range={targetRange}
        bandLow={bandLow}
        bandHigh={bandHigh}
        onClose={() => setDrillDownChannel(null)}
      />
    </Paper>
  );
}
