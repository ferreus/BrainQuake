import { useMemo, useState } from "react";
import { Paper, ScrollArea, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useEiResult } from "../../api/queries/useIctal";
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

  const width = Math.max(600, data.chn_names.length * 22);
  const height = 220;
  const padding = { top: 24, bottom: 30, left: 4, right: 10 };
  const maxEi = Math.max(...data.ei, stats.threshold) * 1.1 || 1;
  const barWidth = (width - padding.left - padding.right) / data.chn_names.length;
  const plotBottom = height - padding.bottom;

  function yFor(v: number) {
    return padding.top + (plotBottom - padding.top) * (1 - v / maxEi);
  }

  return (
    <Paper withBorder p="sm" style={{ flex: 1, minWidth: 0 }}>
      <Title order={6} mb="xs">
        Epileptogenicity Index (EI)
      </Title>
      <ScrollArea>
        <svg width={width} height={height} role="img" aria-label="EI per channel bar chart">
          <line
            x1={padding.left}
            x2={width - padding.right}
            y1={yFor(stats.threshold)}
            y2={yFor(stats.threshold)}
            stroke={colors.threshold}
            strokeDasharray="4 3"
            strokeWidth={1.5}
          />
          <text x={width - padding.right} y={yFor(stats.threshold) - 4} textAnchor="end" fontSize={10} fill={colors.threshold}>
            mean + std
          </text>
          {data.chn_names.map((name, i) => {
            const v = data.ei[i];
            const flagged = v > stats.threshold;
            const x = padding.left + i * barWidth;
            const y = yFor(v);
            return (
              <g
                key={name}
                onClick={() => targetRange && setDrillDownChannel(name)}
                style={{ cursor: targetRange ? "pointer" : "default" }}
              >
                <rect
                  x={x + 1}
                  y={y}
                  width={Math.max(1, barWidth - 2)}
                  height={Math.max(0, plotBottom - y)}
                  fill={flagged ? colors.flagged : colors.bar}
                />
                {flagged && (
                  <text x={x + barWidth / 2} y={y - 4} textAnchor="middle" fontSize={9} fill={colors.text}>
                    {name}
                  </text>
                )}
              </g>
            );
          })}
          <line x1={padding.left} x2={width - padding.right} y1={plotBottom} y2={plotBottom} stroke={colors.threshold} strokeWidth={1} />
        </svg>
      </ScrollArea>
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
