import { useEffect, useRef, useState } from "react";
import { Button, Group, NumberInput, Paper, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import type { EiComputeParams } from "../../api/endpoints";
import { useComputeEi } from "../../api/queries/useIctal";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { recordingParamsQueryKey } from "../../api/queries/useRecordingParams";
import { TERMINAL_JOB_STATES } from "../../api/types";
import type { BaselineTargetSelection } from "../../components/eeg/BaselineTargetLayer";

interface EiComputeFormProps {
  subjectId: number;
  edfArtifactId: number;
  selection: BaselineTargetSelection;
  /** Recording sample rate, used to keep band high below Nyquist. */
  sfreq?: number;
  /** Channels still present after deletions in the trace viewer. Sent as
   * remain_chns: a deleted channel must leave the computation entirely, not
   * just the plot -- it would otherwise stay in the common-average reference
   * and leak into every other channel. */
  remainChannels: string[];
  /** Power-line frequency, owned by the shared EEG viewer state and set from
   * the canvas toolbar. Shared rather than a second local control so the
   * traces on screen and the EI computation can never disagree about it. */
  mainsFreq: number;
  /** This recording's last-submitted ictal params, if any -- seeds band on
   * recording switch. Baseline/target/mains are seeded by the page (into
   * `selection` and the shared viewer state) since this form doesn't own
   * them. */
  initialParams?: EiComputeParams | null;
}

/**
 * The EI computation's own bandpass is separate from the trace-display filter
 * shown in the EEG canvas toolbar (default 60-140Hz for ictal) -- the legacy Qt
 * client kept these two filters distinct too.
 *
 * Band high defaults to 300Hz rather than the legacy 500: on a 1kHz recording
 * 500Hz is exactly Nyquist, which scipy's butter() rejects outright. The server
 * clamps it now, but there's no reason to send a value that needs clamping.
 */
export function EiComputeForm({ subjectId, edfArtifactId, selection, sfreq, remainChannels, mainsFreq, initialParams }: EiComputeFormProps) {
  const [bandLow, setBandLow] = useState(1.0);
  const [bandHigh, setBandHigh] = useState(300.0);
  // 50Hz Europe/Asia, 60Hz North America. The wrong value notches clean signal
  // and leaves the real interference in place.
  const [jobId, setJobId] = useState<number | undefined>();

  // Seed band from this recording's last-submitted params, once per
  // recording -- falls back to the hardcoded defaults above when there are
  // none yet. Guarded to fire once per edfArtifactId, not on every refetch of
  // initialParams (e.g. the post-submit invalidate, or a window-focus
  // refetch), which would otherwise stomp an in-progress unsaved edit.
  const seededEdfId = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!initialParams || seededEdfId.current === edfArtifactId) return;
    seededEdfId.current = edfArtifactId;
    if (initialParams.band_low != null) setBandLow(initialParams.band_low);
    if (initialParams.band_high != null) setBandHigh(initialParams.band_high);
  }, [edfArtifactId, initialParams]);

  const nyquist = sfreq ? sfreq / 2 : undefined;
  const bandHighTooHigh = nyquist != null && bandHigh >= nyquist;
  const bandInverted = bandLow >= bandHigh || bandLow <= 0;

  const computeEi = useComputeEi(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["ei-result", subjectId, edfArtifactId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "EI computation failed", message: finishedJob.progress_message ?? "" });
    }
  });

  /** Edit one endpoint of a typed-in window, leaving the other alone. */
  function updateRange(which: "baseline" | "target", index: 0 | 1, raw: string | number) {
    const value = Number(raw);
    if (raw === "" || Number.isNaN(value)) return;
    const current = which === "baseline" ? selection.baselineRange : selection.targetRange;
    const next: [number, number] = current ? [current[0], current[1]] : [0, 0];
    next[index] = value;
    if (which === "baseline") selection.setBaselineRange(next);
    else selection.setTargetRange(next);
  }

  const rangeError = (() => {
    for (const [label, range] of [
      ["Baseline", selection.baselineRange],
      ["Target", selection.targetRange],
    ] as const) {
      if (!range) continue;
      if (range[0] < 0) return `${label} start must be >= 0.`;
      if (range[1] <= range[0]) return `${label} end must be after ${label.toLowerCase()} start.`;
    }
    return null;
  })();

  async function handleCompute() {
    if (!selection.baselineRange || !selection.targetRange) return;
    try {
      const j = await computeEi.mutateAsync({
        edfArtifactId,
        params: {
          baseline_start: selection.baselineRange[0],
          baseline_end: selection.baselineRange[1],
          target_start: selection.targetRange[0],
          target_end: selection.targetRange[1],
          band_low: bandLow,
          band_high: bandHigh,
          mains_freq: mainsFreq,
          remain_chns: remainChannels,
        },
      });
      setJobId(j.id);
      // Params are saved server-side synchronously with job creation --
      // refetch so the saved-params view reflects this submission right away.
      queryClient.invalidateQueries({ queryKey: recordingParamsQueryKey(subjectId, edfArtifactId) });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start EI computation",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;
  const ready =
    selection.baselineRange != null &&
    selection.targetRange != null &&
    rangeError == null &&
    !bandInverted;

  return (
    <Paper withBorder p="sm" w={300}>
      <Title order={6} mb="xs">
        Compute EI
      </Title>
      <Group gap={4} mb={4}>
        <Button
          size="xs"
          variant={selection.awaitingClick?.startsWith("baseline") ? "filled" : "default"}
          onClick={selection.startBaselineSelect}
        >
          Set Baseline
        </Button>
        <Button
          size="xs"
          variant={selection.awaitingClick?.startsWith("target") ? "filled" : "default"}
          onClick={selection.startTargetSelect}
        >
          Set Target
        </Button>
      </Group>
      {selection.awaitingClick && (
        <Text size="xs" c="blue" mb="xs">
          Click on the trace to mark {selection.awaitingClick.replace("-", " ")}.
        </Text>
      )}
      {/* Editable rather than read-only: clicking the trace fills these in, and
          they can equally be typed straight from a clinical annotation, which is
          the only practical way to enter an exact seizure onset time. */}
      <Text size="xs" fw={500} mt={4}>
        Baseline (s)
      </Text>
      <Group gap={4} grow>
        <NumberInput
          aria-label="Baseline start"
          value={selection.baselineRange?.[0] ?? ""}
          onChange={(v) => updateRange("baseline", 0, v)}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
        <NumberInput
          aria-label="Baseline end"
          value={selection.baselineRange?.[1] ?? ""}
          onChange={(v) => updateRange("baseline", 1, v)}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
      </Group>
      <Text size="xs" fw={500} mt={4}>
        Target (s)
      </Text>
      <Group gap={4} grow>
        <NumberInput
          aria-label="Target start"
          value={selection.targetRange?.[0] ?? ""}
          onChange={(v) => updateRange("target", 0, v)}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
        <NumberInput
          aria-label="Target end"
          value={selection.targetRange?.[1] ?? ""}
          onChange={(v) => updateRange("target", 1, v)}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
      </Group>
      {rangeError && (
        <Text size="xs" c="red" mt={4}>
          {rangeError}
        </Text>
      )}
      <Group gap={4} grow mt={4}>
        <NumberInput label="Band low (Hz)" value={bandLow} onChange={(v) => setBandLow(Number(v) || 0)} size="xs" />
        <NumberInput label="Band high (Hz)" value={bandHigh} onChange={(v) => setBandHigh(Number(v) || 0)} size="xs" />
      </Group>
      <Text size="xs" c="dimmed" mt={4}>
        Mains {mainsFreq}Hz &mdash; set on the trace toolbar, shared with the display filter
      </Text>
      {bandHighTooHigh && (
        <Text size="xs" c="orange" mt={4}>
          Band high must be below Nyquist ({nyquist} Hz); it will be clamped.
        </Text>
      )}
      {bandInverted && (
        <Text size="xs" c="red" mt={4}>
          Band low must be above 0 and below band high.
        </Text>
      )}
      <Button size="xs" mt="sm" fullWidth loading={running} disabled={!ready} onClick={handleCompute}>
        Compute EI
      </Button>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}
