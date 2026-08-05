import { ScrollArea } from "@mantine/core";

export interface ChannelBarChartProps {
  channels: string[];
  values: number[];
  /** Bar fill; per-bar when a function, so callers can flag outliers. */
  barColor: string | ((value: number, index: number) => string);
  axisColor: string;
  labelColor: string;
  /** Channel name drawn above a bar only when this returns true -- labelling
   * every bar is unreadable at typical SEEG channel counts. */
  showLabel?: (value: number, index: number) => boolean;
  /** Dashed reference line with a caption at the right edge. */
  threshold?: { value: number; label: string; color: string };
  onBarClick?: (channel: string, index: number) => void;
  /** Multiplier on the y-axis max, to leave room above the tallest bar for
   * the threshold line and its caption. */
  headroom?: number;
  ariaLabel: string;
}

const HEIGHT = 220;
const PADDING = { top: 24, bottom: 30, left: 4, right: 10 };

/**
 * Horizontally-scrolling per-channel bar chart, shared by the EI (ictal) and
 * HI (interictal) result panels -- both reproduce the same matplotlib bar plot
 * from the legacy Qt client, differing only in palette, the optional threshold
 * line, and whether bars are clickable.
 */
export function ChannelBarChart({
  channels,
  values,
  barColor,
  axisColor,
  labelColor,
  showLabel,
  threshold,
  onBarClick,
  headroom = 1,
  ariaLabel,
}: ChannelBarChartProps) {
  const width = Math.max(600, channels.length * 22);
  const max = Math.max(...values, threshold?.value ?? 0) * headroom || 1;
  const barWidth = (width - PADDING.left - PADDING.right) / channels.length;
  const plotBottom = HEIGHT - PADDING.bottom;

  const yFor = (v: number) => PADDING.top + (plotBottom - PADDING.top) * (1 - v / max);

  return (
    <ScrollArea>
      <svg width={width} height={HEIGHT} role="img" aria-label={ariaLabel}>
        {threshold && (
          <>
            <line
              x1={PADDING.left}
              x2={width - PADDING.right}
              y1={yFor(threshold.value)}
              y2={yFor(threshold.value)}
              stroke={threshold.color}
              strokeDasharray="4 3"
              strokeWidth={1.5}
            />
            <text
              x={width - PADDING.right}
              y={yFor(threshold.value) - 4}
              textAnchor="end"
              fontSize={10}
              fill={threshold.color}
            >
              {threshold.label}
            </text>
          </>
        )}
        {channels.map((name, i) => {
          const v = values[i];
          const x = PADDING.left + i * barWidth;
          const y = yFor(v);
          return (
            <g
              key={name}
              onClick={onBarClick ? () => onBarClick(name, i) : undefined}
              style={{ cursor: onBarClick ? "pointer" : "default" }}
            >
              <rect
                x={x + 1}
                y={y}
                width={Math.max(1, barWidth - 2)}
                height={Math.max(0, plotBottom - y)}
                fill={typeof barColor === "function" ? barColor(v, i) : barColor}
              />
              {showLabel?.(v, i) && (
                <text x={x + barWidth / 2} y={y - 4} textAnchor="middle" fontSize={9} fill={labelColor}>
                  {name}
                </text>
              )}
            </g>
          );
        })}
        <line
          x1={PADDING.left}
          x2={width - PADDING.right}
          y1={plotBottom}
          y2={plotBottom}
          stroke={axisColor}
          strokeWidth={1}
        />
      </svg>
    </ScrollArea>
  );
}
