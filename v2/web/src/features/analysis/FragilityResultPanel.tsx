import { useState } from "react";
import { Badge, Group, Paper, Select, Text, Title, useComputedColorScheme } from "@mantine/core";
import { useAnalysisAggregate, useFragilityResult } from "../../api/queries/useAnalysis";
import { ChannelTimeHeatmap } from "./ChannelTimeHeatmap";
import { RankedChannelChart } from "./RankedChannelChart";
import { ShaftRankingPanel } from "./ShaftRankingPanel";
import type { ProcessPaneProps } from "./processes";

const COLORS = {
  light: { bar: "#8a3fa0", flagged: "#d03b3b", axis: "#52514e", text: "#0b0b0b" },
  dark: { bar: "#b16bc8", flagged: "#e66767", axis: "#c3c2b7", text: "#ffffff" },
};

export function FragilityResultPanel({ subjectId, edfArtifactId }: ProcessPaneProps) {
  const [runKey, setRunKey] = useState<string | undefined>();
  // A clip can hold several marked seizures, each its own run. Reuses the
  // aggregate rather than adding a per-recording runs endpoint.
  const { data: aggregate } = useAnalysisAggregate(subjectId, "fragility", 20);
  const runsHere = (aggregate?.runs ?? []).filter((r) => r.edf_artifact_id === edfArtifactId);
  // Falls back to the first listed run, not to undefined: undefined makes the
  // server pick the *newest* run while the Select still displays runsHere[0],
  // so the heatmap and the label would describe different seizures.
  const activeKey =
    runKey && runsHere.some((r) => r.run_key === runKey) ? runKey : runsHere[0]?.run_key;

  const { data, isLoading, isError } = useFragilityResult(subjectId, edfArtifactId, activeKey, true);
  const scheme = useComputedColorScheme("light");

  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <Paper withBorder p="sm">
        <Group gap="xs" mb="xs" wrap="nowrap">
          <Title order={6}>Neural fragility</Title>
          {data && (
            <>
              <Badge size="xs" variant="light" color="gray">
                {data.method}
              </Badge>
              {/* The linear fit is the whole method: a low R2 means the ranking
                  describes noise rather than dynamics. */}
              <Badge size="xs" variant="light" color={data.median_r2 < 0.8 ? "orange" : "green"}>
                median R² {data.median_r2.toFixed(3)}
              </Badge>
            </>
          )}
        </Group>

        {runsHere.length > 1 && (
          <Select
            size="xs"
            w={260}
            mb="xs"
            label="Seizure"
            allowDeselect={false}
            value={activeKey}
            onChange={(v) => setRunKey(v ?? undefined)}
            data={runsHere.map((r) => ({
              value: r.run_key,
              label: `${r.label ?? "onset"} @ ${(r.onset_s ?? 0).toFixed(1)}s`,
            }))}
          />
        )}

        {isLoading && null}
        {(isError || !data) && !isLoading && (
          <Text size="xs" c="dimmed">
            Not computed yet for this recording.
          </Text>
        )}

        {data && (
          <>
            <ChannelTimeHeatmap
              matrix={data.fragility_matrix}
              channels={data.chn_names}
              startTimes={data.start_times}
            />
            <Text size="xs" c="dimmed" mt={4}>
              Fragility per contact over time; brighter is more fragile. Scores below average the
              windows in [0, {String(data.params?.eval_end ?? 5)}] s after the onset.
            </Text>
            <RankedChannelChart
              names={data.chn_names}
              values={data.chn_names.map((n) => data.channel_scores[n] ?? null)}
              colors={COLORS[scheme]}
              ariaLabel="Fragility score per contact bar chart"
            />
          </>
        )}
      </Paper>

      <ShaftRankingPanel subjectId={subjectId} process="fragility" />
    </div>
  );
}
