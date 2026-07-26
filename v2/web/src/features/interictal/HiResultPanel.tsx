import { Paper, ScrollArea, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useHfoResult } from "../../api/queries/useInterictal";

interface HiResultPanelProps {
  subjectId: number;
  edfArtifactId: number;
}

// Categorical green for the HI bars (matches client_inter.py's (50,168,82)
// bar color), validated against both color schemes per the dataviz skill.
const COLORS = {
  light: { bar: "#2f9e5a", axis: "#52514e", text: "#0b0b0b" },
  dark: { bar: "#43b56e", axis: "#c3c2b7", text: "#ffffff" },
};

/** Per-channel high-frequency-events count bar chart, mirroring
 * client_inter.py's HI_plot_func (matplotlib bar + per-bar channel label). */
export function HiResultPanel({ subjectId, edfArtifactId }: HiResultPanelProps) {
  const { data, isLoading, isError } = useHfoResult(subjectId, edfArtifactId, true);
  const scheme = useComputedColorScheme("light");
  const colors = COLORS[scheme];

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

  const width = Math.max(600, data.chn_names.length * 22);
  const height = 220;
  const padding = { top: 24, bottom: 30, left: 4, right: 10 };
  const maxCount = Math.max(...data.event_counts, 1);
  const barWidth = (width - padding.left - padding.right) / data.chn_names.length;
  const plotBottom = height - padding.bottom;

  function yFor(v: number) {
    return padding.top + (plotBottom - padding.top) * (1 - v / maxCount);
  }

  return (
    <Paper withBorder p="sm" style={{ flex: 1, minWidth: 0 }}>
      <Title order={6} mb="xs">
        High-frequency events (HI)
      </Title>
      <ScrollArea>
        <svg width={width} height={height} role="img" aria-label="HFO event count per channel bar chart">
          {data.chn_names.map((name, i) => {
            const v = data.event_counts[i];
            const x = padding.left + i * barWidth;
            const y = yFor(v);
            return (
              <g key={name}>
                <rect x={x + 1} y={y} width={Math.max(1, barWidth - 2)} height={Math.max(0, plotBottom - y)} fill={colors.bar} />
                {v > 0 && (
                  <text x={x + barWidth / 2} y={y - 4} textAnchor="middle" fontSize={9} fill={colors.text}>
                    {name}
                  </text>
                )}
              </g>
            );
          })}
          <line x1={padding.left} x2={width - padding.right} y1={plotBottom} y2={plotBottom} stroke={colors.axis} strokeWidth={1} />
        </svg>
      </ScrollArea>
      <Text size="xs" c="dimmed" mt={4}>
        Toggle &ldquo;Show HFO detections&rdquo; above to overlay detected events on the traces.
      </Text>
    </Paper>
  );
}
