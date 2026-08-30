import { useEffect, useRef, useState } from "react";
import { Button, Group, NumberInput, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import type { HfoComputeParams } from "../../api/endpoints";
import { useComputeHfo } from "../../api/queries/useInterictal";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { recordingParamsQueryKey } from "../../api/queries/useRecordingParams";
import { TERMINAL_JOB_STATES } from "../../api/types";

interface HfoComputeFormProps {
  subjectId: number;
  edfArtifactId: number;
  /** HFO/ripple band -- taken from the trace-display filter (default 80-250Hz
   * for interictal), matching client_inter.py which reused self.band_low/high. */
  bandLow: number;
  bandHigh: number;
  /** Channels not deleted in the channel list; sent as remain_chns so the
   * server only scans the same set the user is looking at. */
  remainChannels: string[];
  /** Recording sample rate and length, for Nyquist and window validation. */
  sfreq?: number;
  durationSec?: number;
  /** This recording's last-submitted interictal params, if any -- takes
   * priority over the display-filter band seed below, once per recording. */
  initialParams?: HfoComputeParams | null;
}

/**
 * Queues an HFO (high-frequency-events) detection job and polls it. Thresholds
 * mirror client_inter.py's four line-edits (rel/abs envelope thresholds, min
 * gap to merge, min duration to keep). On finish it invalidates the hfo-result
 * query so HiResultPanel and the canvas overlay pick up the detections.
 */
export function HfoComputeForm({
  subjectId,
  edfArtifactId,
  bandLow,
  bandHigh,
  remainChannels,
  sfreq,
  durationSec,
  initialParams,
}: HfoComputeFormProps) {
  const [relThresh, setRelThresh] = useState(2.0);
  const [absThresh, setAbsThresh] = useState(2.0);
  const [minGap, setMinGap] = useState(20.0);
  const [minLast, setMinLast] = useState(50.0);
  // Seeded from the display filter but independently editable, so an exact band
  // can be typed here without disturbing what the traces are rendered with.
  // Changing the display filter re-seeds these.
  const [band, setBand] = useState<[number, number]>([bandLow, bandHigh]);
  useEffect(() => setBand([bandLow, bandHigh]), [bandLow, bandHigh]);
  // 50Hz Europe/Asia, 60Hz North America. Critical here: on 60Hz mains the 180
  // and 240Hz harmonics land inside the 80-250Hz ripple band, so a wrong value
  // manufactures HFO detections.
  const [mainsFreq, setMainsFreq] = useState(50.0);
  // Blank = whole recording, the legacy behavior.
  const [startTime, setStartTime] = useState<number | "">("");
  const [endTime, setEndTime] = useState<number | "">("");
  const [jobId, setJobId] = useState<number | undefined>();

  // This recording's last-submitted params take priority over the display-
  // filter band seed above, once per recording -- guarded so a later refetch
  // (post-submit invalidate, window-focus refetch) doesn't stomp an
  // in-progress unsaved edit.
  const seededEdfId = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (!initialParams || seededEdfId.current === edfArtifactId) return;
    seededEdfId.current = edfArtifactId;
    if (initialParams.band_low != null && initialParams.band_high != null) {
      setBand([initialParams.band_low, initialParams.band_high]);
    }
    if (initialParams.rel_thresh != null) setRelThresh(initialParams.rel_thresh);
    if (initialParams.abs_thresh != null) setAbsThresh(initialParams.abs_thresh);
    if (initialParams.min_gap != null) setMinGap(initialParams.min_gap);
    if (initialParams.min_last != null) setMinLast(initialParams.min_last);
    if (initialParams.mains_freq != null) setMainsFreq(initialParams.mains_freq);
    setStartTime(initialParams.start_time ?? "");
    setEndTime(initialParams.end_time ?? "");
  }, [edfArtifactId, initialParams]);

  const nyquist = sfreq ? sfreq / 2 : undefined;
  const bandHighTooHigh = nyquist != null && band[1] >= nyquist;
  const bandInverted = band[0] <= 0 || band[0] >= band[1];

  const windowError = (() => {
    const s = startTime === "" ? null : Number(startTime);
    const e = endTime === "" ? null : Number(endTime);
    if (s != null && s < 0) return "Start must be >= 0.";
    if (s != null && e != null && e <= s) return "End must be after start.";
    if (durationSec != null) {
      if (s != null && s >= durationSec) return `Start is past the end of the recording (${durationSec.toFixed(1)}s).`;
      if (e != null && e > durationSec) return `End is past the end of the recording (${durationSec.toFixed(1)}s).`;
    }
    return null;
  })();

  const computeHfo = useComputeHfo(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["hfo-result", subjectId, edfArtifactId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "HFO computation failed", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleCompute() {
    try {
      const j = await computeHfo.mutateAsync({
        edfArtifactId,
        params: {
          band_low: band[0],
          band_high: band[1],
          rel_thresh: relThresh,
          abs_thresh: absThresh,
          min_gap: minGap,
          min_last: minLast,
          remain_chns: remainChannels,
          mains_freq: mainsFreq,
          start_time: startTime === "" ? null : Number(startTime),
          end_time: endTime === "" ? null : Number(endTime),
        },
      });
      setJobId(j.id);
      // Params are saved server-side synchronously with job creation --
      // refetch so the saved-params view reflects this submission right away.
      queryClient.invalidateQueries({ queryKey: recordingParamsQueryKey(subjectId, edfArtifactId) });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start HFO computation",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <>
      <Text size="xs" c="dimmed" mb="xs">
        {remainChannels.length} channels. Band seeded from the display filter; editable here.
      </Text>
      <Text size="xs" fw={500}>
        Ripple band (Hz)
      </Text>
      <Group gap={4} grow>
        <NumberInput
          aria-label="Band low"
          value={band[0]}
          onChange={(v) => setBand([Number(v) || 0, band[1]])}
          size="xs"
        />
        <NumberInput
          aria-label="Band high"
          value={band[1]}
          onChange={(v) => setBand([band[0], Number(v) || 0])}
          size="xs"
        />
      </Group>
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
      <NumberInput
        label="Mains (Hz)"
        description="50 Europe/Asia, 60 North America"
        value={mainsFreq}
        onChange={(v) => setMainsFreq(Number(v) || 0)}
        size="xs"
        mt={4}
      />
      <Text size="xs" fw={500} mt={4}>
        Analysis window (s)
      </Text>
      <Group gap={4} grow>
        <NumberInput
          aria-label="Window start"
          placeholder="start"
          value={startTime}
          onChange={(v) => setStartTime(v === "" ? "" : Number(v))}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
        <NumberInput
          aria-label="Window end"
          placeholder="end"
          value={endTime}
          onChange={(v) => setEndTime(v === "" ? "" : Number(v))}
          size="xs"
          step={0.1}
          decimalScale={3}
        />
      </Group>
      <Text size="xs" c="dimmed">
        Blank = whole recording.
      </Text>
      {windowError && (
        <Text size="xs" c="red" mt={4}>
          {windowError}
        </Text>
      )}
      <NumberInput label="Rel. threshold (× channel median)" value={relThresh} onChange={(v) => setRelThresh(Number(v) || 0)} size="xs" step={0.5} mt={4} />
      <NumberInput label="Abs. threshold (× global median)" value={absThresh} onChange={(v) => setAbsThresh(Number(v) || 0)} size="xs" step={0.5} mt={4} />
      <NumberInput label="Min gap (ms, merge events)" value={minGap} onChange={(v) => setMinGap(Number(v) || 0)} size="xs" mt={4} />
      <NumberInput label="Min duration (ms, keep event)" value={minLast} onChange={(v) => setMinLast(Number(v) || 0)} size="xs" mt={4} />
      <Button
        size="xs"
        mt="sm"
        fullWidth
        loading={running}
        disabled={windowError != null || bandInverted}
        onClick={handleCompute}
      >
        Compute HFO
      </Button>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </>
  );
}
