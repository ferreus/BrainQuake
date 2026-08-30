import { ScrollArea } from "@mantine/core";

export interface ChannelChartColors {
  bar: string;
  flagged: string;
  axis: string;
  text: string;
}

interface RankedChannelChartProps {
  names: string[];
  /** null for a dead channel: no bar, rather than a zero-height one. */
  values: (number | null)[];
  colors: ChannelChartColors;
  ariaLabel: string;
  /** Draws a dashed rule and colours anything above it as flagged. */
  threshold?: number | null;
  thresholdLabel?: string;
  /** Which bars get their channel name printed. Default: the flagged ones. */
  labelBar?: (value: number, flagged: boolean) => boolean;
  onBarClick?: (name: string) => void;
}

const HEIGHT = 220;
const PADDING = { top: 24, bottom: 30, left: 4, right: 10 };

/** Per-channel bar chart shared by every process that produces channel scores
 * (EI, HFO event counts, fragility). The two panels that predated it had
 * identical geometry and differed only in chrome. */
export function RankedChannelChart({
  names, values, colors, ariaLabel, threshold, thresholdLabel, labelBar, onBarClick,
}: RankedChannelChartProps) {
  const finite = values.filter((v): v is number => v != null && Number.isFinite(v));
  const width = Math.max(600, names.length * 22);
  const max = Math.max(...finite, threshold ?? 0) * 1.1 || 1;
  const barWidth = (width - PADDING.left - PADDING.right) / Math.max(names.length, 1);
  const plotBottom = HEIGHT - PADDING.bottom;
  const yFor = (v: number) => PADDING.top + (plotBottom - PADDING.top) * (1 - v / max);
  const shouldLabel = labelBar ?? ((_v: number, flagged: boolean) => flagged);

  return (
    <ScrollArea>
      <svg width={width} height={HEIGHT} role="img" aria-label={ariaLabel}>
        {threshold != null && (
          <>
            <line
              x1={PADDING.left} x2={width - PADDING.right}
              y1={yFor(threshold)} y2={yFor(threshold)}
              stroke={colors.axis} strokeDasharray="4 3" strokeWidth={1.5}
            />
            {thresholdLabel && (
              <text
                x={width - PADDING.right} y={yFor(threshold) - 4}
                textAnchor="end" fontSize={10} fill={colors.axis}
              >
                {thresholdLabel}
              </text>
            )}
          </>
        )}
        {names.map((name, i) => {
          const raw = values[i];
          const v = raw != null && Number.isFinite(raw) ? raw : 0;
          const flagged = threshold != null && v > threshold;
          const x = PADDING.left + i * barWidth;
          const y = yFor(v);
          return (
            <g
              key={name}
              onClick={onBarClick ? () => onBarClick(name) : undefined}
              style={{ cursor: onBarClick ? "pointer" : "default" }}
            >
              <rect
                x={x + 1} y={y}
                width={Math.max(1, barWidth - 2)}
                height={Math.max(0, plotBottom - y)}
                fill={flagged ? colors.flagged : colors.bar}
              />
              {shouldLabel(v, flagged) && (
                <text x={x + barWidth / 2} y={y - 4} textAnchor="middle" fontSize={9} fill={colors.text}>
                  {name}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={PADDING.left} x2={width - PADDING.right}
          y1={plotBottom} y2={plotBottom} stroke={colors.axis} strokeWidth={1}
        />
      </svg>
    </ScrollArea>
  );
}
