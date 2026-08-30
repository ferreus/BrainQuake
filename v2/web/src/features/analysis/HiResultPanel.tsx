import { Paper, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useHfoResult } from "../../api/queries/useInterictal";
import { RankedChannelChart } from "./RankedChannelChart";

interface HiResultPanelProps {
  subjectId: number;
  edfArtifactId: number;
}

// Categorical green for the HI bars (matches client_inter.py's (50,168,82)
// bar color), validated against both color schemes per the dataviz skill.
const COLORS = {
  light: { bar: "#2f9e5a", flagged: "#2f9e5a", axis: "#52514e", text: "#0b0b0b" },
  dark: { bar: "#43b56e", flagged: "#43b56e", axis: "#c3c2b7", text: "#ffffff" },
};

/** Per-channel high-frequency-events count bar chart, mirroring
 * client_inter.py's HI_plot_func (matplotlib bar + per-bar channel label). */
export function HiResultPanel({ subjectId, edfArtifactId }: HiResultPanelProps) {
  const { data, isLoading, isError } = useHfoResult(subjectId, edfArtifactId, true);
  const scheme = useComputedColorScheme("light");

  if (isLoading) return null;
  if (isError || !data || data.chn_names.length === 0) {
    return (
      <Paper withBorder p="sm" style={{ flex: 1 }}>
        <Title order={6} mb="xs">
          High-frequency events (HI)
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
        High-frequency events (HI)
      </Title>
      <RankedChannelChart
        names={data.chn_names}
        values={data.event_counts}
        colors={COLORS[scheme]}
        ariaLabel="HFO event count per channel bar chart"
        labelBar={(v) => v > 0}
      />
      <Text size="xs" c="dimmed" mt={4}>
        Toggle &ldquo;Show HFO detections&rdquo; above to overlay detected events on the traces.
      </Text>
    </Paper>
  );
}
