import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Checkbox, Group, NumberInput, Select, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { getRecordingParams } from "../../api/endpoints";
import { useRunAnalysis } from "../../api/queries/useAnalysis";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useJobs } from "../../api/queries/useJobs";
import { recordingParamsQueryKey } from "../../api/queries/useRecordingParams";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { edfDisplayName } from "../../lib/edfName";
import type { ProcessPaneProps } from "./processes";

/** Defaults mirror run_frag.R's PRE/POST/ICTAL_END and Li et al.'s 250ms/125ms
 * window. They have to: a run on a different window compares windows, not
 * implementations (v2/tools/verify_fragility_bella.py). */
const DEFAULTS = { pre: 20, post: 10, evalEnd: 5, winS: 0.25, stepS: 0.125 };

const MANUAL = "manual";
const markKey = (edfId: number, index: number | typeof MANUAL) => `${edfId}:${index}`;

/**
 * Fragility runs over a *set of seizures*, not a set of recordings: each run
 * analyses one `[onset-pre, onset+post]` window, and one clip can hold several
 * marked seizures. Rows are therefore (recording, mark) pairs -- the same shape
 * as the CLI manifest's `label,edf_path,onset`.
 *
 * The filter is the workhorse: clinical clips carry ~70 marks, of which a
 * handful are seizures ("in bed with dad" is a real one), so typing `SZ` and
 * hitting Select all shown is the difference between one click and seventy.
 * It is a plain substring match, never a guess at which marks are seizures.
 */
export function FragilityComputeForm({ subjectId, edfArtifactId, remainChannels }: ProcessPaneProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const recordings = useMemo(
    () => (artifacts ?? []).filter((a) => a.kind === "raw_edf"),
    [artifacts],
  );

  const [method, setMethod] = useState<"extended" | "ezfragility">("extended");
  const [pre, setPre] = useState(DEFAULTS.pre);
  const [post, setPost] = useState(DEFAULTS.post);
  const [evalEnd, setEvalEnd] = useState(DEFAULTS.evalEnd);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set([edfArtifactId]));
  const [manualOnsets, setManualOnsets] = useState<Record<number, number | null>>({});

  const paramQueries = useQueries({
    queries: recordings.map((a) => ({
      queryKey: recordingParamsQueryKey(subjectId, a.id),
      queryFn: () => getRecordingParams(subjectId, a.id),
      staleTime: 60_000,
    })),
  });

  const annotationsById = useMemo(() => {
    const map: Record<number, { onset: number; description: string }[]> = {};
    recordings.forEach((a, i) => {
      map[a.id] = paramQueries[i]?.data?.annotations ?? [];
    });
    return map;
  }, [recordings, paramQueries]);

  const needle = filter.trim().toLowerCase();
  /** Marks passing the filter, per recording, keeping each mark's real index so
   * the key survives filtering (onsets are not unique -- clips carry several
   * marks on one timestamp). */
  const shownById = useMemo(() => {
    const map: Record<number, { index: number; onset: number; description: string }[]> = {};
    for (const a of recordings) {
      map[a.id] = (annotationsById[a.id] ?? [])
        .map((an, index) => ({ index, ...an }))
        .filter((an) => !needle || an.description.toLowerCase().includes(needle));
    }
    return map;
  }, [recordings, annotationsById, needle]);

  const allShownKeys = useMemo(
    () => recordings.flatMap((a) => shownById[a.id].map((m) => markKey(a.id, m.index))),
    [recordings, shownById],
  );

  /** Selected keys resolved back to submittable runs. */
  const runs = useMemo(() => {
    const out: { edf_artifact_id: number; onset: number; label: string | null }[] = [];
    for (const a of recordings) {
      if (selected.has(markKey(a.id, MANUAL))) {
        const v = manualOnsets[a.id];
        if (v != null) out.push({ edf_artifact_id: a.id, onset: v, label: null });
      }
      for (const an of annotationsById[a.id] ?? []) {
        const idx = (annotationsById[a.id] ?? []).indexOf(an);
        if (selected.has(markKey(a.id, idx))) {
          out.push({ edf_artifact_id: a.id, onset: an.onset, label: an.description || null });
        }
      }
    }
    return out;
  }, [recordings, annotationsById, selected, manualOnsets]);

  /** Two marks closer together than the crop analyse largely the same signal and
   * would vote twice for it. Surfaced, not blocked -- it can be intentional. */
  const overlaps = useMemo(() => {
    const byRec: Record<number, number[]> = {};
    runs.forEach((r) => (byRec[r.edf_artifact_id] ??= []).push(r.onset));
    let n = 0;
    for (const onsets of Object.values(byRec)) {
      const sorted = [...onsets].sort((a, b) => a - b);
      for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] - sorted[i - 1] < pre + post) n++;
      }
    }
    return n;
  }, [runs, pre, post]);

  const runAnalysis = useRunAnalysis(subjectId, "fragility");
  const queryClient = useQueryClient();

  // A batch is N jobs, so there is no single id to hand useJobPolling. useJobs
  // already polls every 3s for the Jobs drawer; ride that instead of adding a
  // variable number of hooks. Without this nothing refreshed the aggregate once
  // a run finished, and a failed run produced no notification at all.
  const [batchJobIds, setBatchJobIds] = useState<number[]>([]);
  const { data: allJobs } = useJobs({ subjectId });
  const settledCount = useRef(0);
  useEffect(() => {
    if (batchJobIds.length === 0 || !allJobs) return;
    const mine = allJobs.filter((j) => batchJobIds.includes(j.id));
    const settled = mine.filter((j) => TERMINAL_JOB_STATES.has(j.state));
    if (settled.length === settledCount.current) return;
    settledCount.current = settled.length;

    // Refresh after every run, so the ranking really does fill in as they land.
    queryClient.invalidateQueries({ queryKey: ["analysis-aggregate", subjectId] });
    queryClient.invalidateQueries({ queryKey: ["fragility-result", subjectId] });

    if (settled.length < batchJobIds.length) return;
    const failed = settled.filter((j) => j.state === "failed");
    setBatchJobIds([]);
    settledCount.current = 0;
    if (failed.length > 0) {
      notifications.show({
        color: "red",
        title: `${failed.length} of ${settled.length} fragility run${settled.length === 1 ? "" : "s"} failed`,
        message: failed[0].progress_message ?? "See the Jobs drawer for the log.",
      });
    }
  }, [allJobs, batchJobIds, queryClient, subjectId]);
  const ready = runs.length > 0 && pre >= 0 && post > 0 && evalEnd > 0;

  function toggle(key: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  function setMany(keys: string[], on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (on ? next.add(k) : next.delete(k)));
      return next;
    });
  }

  async function handleRun() {
    try {
      const jobs = await runAnalysis.mutateAsync({
        params: {
          pre, post, eval_end: evalEnd, method,
          win_s: DEFAULTS.winS, step_s: DEFAULTS.stepS,
          // Deliberately one channel set for the whole batch, taken from the
          // viewed recording: pooling votes across seizures is only meaningful
          // if every run analysed the same montage (the CAR is over exactly
          // these channels, and shaft sizes must match). A recording that lacks
          // one of these names fails its job rather than being analysed on a
          // different montage -- surfaced by the batch watcher above.
          remain_chns: remainChannels,
        },
        runs: runs.map((r) => ({
          edf_artifact_id: r.edf_artifact_id,
          marks: { onset_s: r.onset, onset_label: r.label },
        })),
      });
      setBatchJobIds(jobs.map((j) => j.id));
      settledCount.current = 0;
      notifications.show({
        color: "blue",
        title: `Queued ${jobs.length} fragility run${jobs.length === 1 ? "" : "s"}`,
        message: "They run one at a time; the shaft ranking updates as each finishes.",
      });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to queue fragility",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  return (
    <>
      <Group justify="space-between" mb={4}>
        <Text size="xs" fw={600}>
          Seizures to run
        </Text>
        <Text size="xs" c={runs.length > 0 ? undefined : "dimmed"}>
          {runs.length} selected
        </Text>
      </Group>

      <TextInput
        size="xs"
        placeholder="Filter marks, e.g. SZ"
        value={filter}
        onChange={(e) => {
          setFilter(e.currentTarget.value);
          // Auto-expand what the filter matched, so results are visible at once.
          if (e.currentTarget.value.trim()) setExpanded(new Set(recordings.map((a) => a.id)));
        }}
      />
      <Group gap={4} mt={4}>
        <Button size="compact-xs" variant="default" onClick={() => setMany(allShownKeys, true)}>
          Select all shown
        </Button>
        <Button size="compact-xs" variant="default" onClick={() => setSelected(new Set())}>
          Clear
        </Button>
      </Group>

      <Stack gap={2} mt={6} mah={260} style={{ overflowY: "auto" }}>
        {recordings.length === 0 && (
          <Text size="xs" c="dimmed">
            No recordings imported yet.
          </Text>
        )}
        {recordings.map((a) => {
          const shown = shownById[a.id];
          const total = (annotationsById[a.id] ?? []).length;
          const isOpen = expanded.has(a.id);
          const keys = shown.map((m) => markKey(a.id, m.index));
          const nSel = keys.filter((k) => selected.has(k)).length;
          return (
            <div key={a.id}>
              <Group gap={4} wrap="nowrap">
                <Checkbox
                  size="xs"
                  aria-label={`Select all marks in ${edfDisplayName(a)}`}
                  checked={keys.length > 0 && nSel === keys.length}
                  indeterminate={nSel > 0 && nSel < keys.length}
                  disabled={keys.length === 0}
                  onChange={(e) => setMany(keys, e.currentTarget.checked)}
                />
                <UnstyledButton
                  style={{ flex: 1, minWidth: 0 }}
                  onClick={() =>
                    setExpanded((prev) => {
                      const next = new Set(prev);
                      if (next.has(a.id)) next.delete(a.id);
                      else next.add(a.id);
                      return next;
                    })
                  }
                >
                  <Text size="xs" truncate>
                    {isOpen ? "▾" : "▸"} {edfDisplayName(a)}
                  </Text>
                </UnstyledButton>
                <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                  {total === 0 ? "no marks" : `${shown.length}/${total}`}
                </Text>
              </Group>

              {isOpen && (
                <Stack gap={0} style={{ marginLeft: 22 }}>
                  {shown.map((m) => (
                    <Checkbox
                      key={m.index}
                      size="xs"
                      mt={2}
                      checked={selected.has(markKey(a.id, m.index))}
                      onChange={(e) => toggle(markKey(a.id, m.index), e.currentTarget.checked)}
                      label={
                        <Text size="xs" span>
                          {m.description || "(unnamed)"}{" "}
                          <Text size="xs" span c="dimmed">
                            @ {m.onset.toFixed(1)}s
                          </Text>
                        </Text>
                      }
                    />
                  ))}
                  {total === 0 && (
                    <Group gap={4} wrap="nowrap" mt={2}>
                      <Checkbox
                        size="xs"
                        aria-label={`Use a manual onset for ${edfDisplayName(a)}`}
                        checked={selected.has(markKey(a.id, MANUAL))}
                        onChange={(e) => toggle(markKey(a.id, MANUAL), e.currentTarget.checked)}
                      />
                      <NumberInput
                        size="xs"
                        aria-label={`Onset seconds for ${edfDisplayName(a)}`}
                        placeholder="onset (s)"
                        value={manualOnsets[a.id] ?? ""}
                        onChange={(v) =>
                          setManualOnsets((prev) => ({
                            ...prev, [a.id]: v === "" ? null : Number(v),
                          }))
                        }
                        step={0.1}
                        decimalScale={3}
                        min={0}
                      />
                    </Group>
                  )}
                  {total > 0 && shown.length === 0 && (
                    <Text size="xs" c="dimmed" mt={2}>
                      No marks match the filter.
                    </Text>
                  )}
                </Stack>
              )}
            </div>
          );
        })}
      </Stack>

      <Group gap={4} grow mt="sm">
        <NumberInput label="Pre (s)" size="xs" value={pre} onChange={(v) => setPre(Number(v) || 0)} min={0} />
        <NumberInput label="Post (s)" size="xs" value={post} onChange={(v) => setPost(Number(v) || 0)} min={0} />
      </Group>
      <NumberInput
        label="Score windows in [0, x] s"
        size="xs" mt={4} min={0}
        value={evalEnd}
        onChange={(v) => setEvalEnd(Number(v) || 0)}
      />

      <Text size="xs" fw={500} mt="sm">
        Method
      </Text>
      <Select
        size="xs" mt={4} allowDeselect={false}
        value={method}
        onChange={(v) => v && setMethod(v as "extended" | "ezfragility")}
        data={[
          { value: "extended", label: "Extended (default)" },
          { value: "ezfragility", label: "EZFragility (literature parity)" },
        ]}
      />
      <Text size="xs" c="dimmed" mt={4}>
        {method === "extended"
          ? "Sees DC modes the published quarter-arc contour drops, and high-passes at 0.5Hz."
          : "Reproduces the R package exactly; unfiltered, for comparison with published results."}
      </Text>

      {overlaps > 0 && (
        <Alert color="orange" p="xs" mt="xs">
          <Text size="xs">
            {overlaps} pair{overlaps === 1 ? "" : "s"} of selected marks are less than{" "}
            {pre + post}s apart, so their windows overlap and largely the same signal votes twice.
          </Text>
        </Alert>
      )}

      <Button
        size="xs" mt="sm" fullWidth
        loading={runAnalysis.isPending}
        disabled={!ready}
        onClick={handleRun}
      >
        Run on {runs.length} seizure{runs.length === 1 ? "" : "s"}
      </Button>
    </>
  );
}
