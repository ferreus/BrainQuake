import { useMemo, useState } from "react";
import type { Dispatch } from "react";
import { Group, Stack, Switch } from "@mantine/core";
import { useHfoResult } from "../../api/queries/useInterictal";
import { EdfWorkspace } from "../../components/eeg/EdfWorkspace";
import { EegCanvas } from "../../components/eeg/EegCanvas";
import { EegChannelList } from "../../components/eeg/EegChannelList";
import { HfoComputeForm } from "./HfoComputeForm";
import { HiResultPanel } from "./HiResultPanel";
import type { EdfMeta } from "../../api/endpoints";
import type { EegViewerAction, EegViewerState } from "../../components/eeg/useEegViewerState";

interface OverlaySwitchProps {
  subjectId: number;
  edfArtifactId: number;
  checked: boolean;
  onChange: (checked: boolean) => void;
}

/** Only offered once detections actually exist to overlay. */
function HfoOverlaySwitch({ subjectId, edfArtifactId, checked, onChange }: OverlaySwitchProps) {
  const { data: hfoResult } = useHfoResult(subjectId, edfArtifactId, true);
  if (!hfoResult) return null;
  return (
    <Switch
      label="Show HFO detections"
      size="sm"
      checked={checked}
      onChange={(e) => onChange(e.currentTarget.checked)}
    />
  );
}

interface WorkspaceProps {
  subjectId: number;
  edfArtifactId: number;
  meta: EdfMeta;
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
  showOverlay: boolean;
}

function InterictalWorkspace({ subjectId, edfArtifactId, meta, state, dispatch, showOverlay }: WorkspaceProps) {
  const { data: hfoResult } = useHfoResult(subjectId, edfArtifactId, true);

  const remainChannels = useMemo(
    () => meta.channels.filter((c) => !state.excludedChannels.has(c)),
    [meta.channels, state.excludedChannels],
  );

  // channel name -> detected [start,end] events, consumed by EegCanvas' overlay.
  const eventOverlays = useMemo(() => {
    if (!showOverlay || !hfoResult) return undefined;
    const map: Record<string, [number, number][]> = {};
    hfoResult.chn_names.forEach((name, i) => {
      map[name] = hfoResult.event_times[i] ?? [];
    });
    return map;
  }, [showOverlay, hfoResult]);

  return (
    <>
      <Group align="flex-start" gap="md" wrap="nowrap">
        <EegCanvas
          subjectId={subjectId}
          edfArtifactId={edfArtifactId}
          state={state}
          dispatch={dispatch}
          eventOverlays={eventOverlays}
        />
        <Stack w={300} gap="sm">
          <EegChannelList
            channels={meta.channels}
            excludedChannels={state.excludedChannels}
            onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
          />
          <HfoComputeForm
            subjectId={subjectId}
            edfArtifactId={edfArtifactId}
            bandLow={state.filterBandLow}
            bandHigh={state.filterBandHigh}
            remainChannels={remainChannels}
          />
        </Stack>
      </Group>
      <HiResultPanel subjectId={subjectId} edfArtifactId={edfArtifactId} />
    </>
  );
}

interface InterictalPageProps {
  subjectId: number;
}

export function InterictalPage({ subjectId }: InterictalPageProps) {
  const [showOverlay, setShowOverlay] = useState(false);

  return (
    <EdfWorkspace
      subjectId={subjectId}
      mode="interictal"
      emptyText="Import an interictal EDF recording to get started."
      toolbarExtra={({ edfArtifactId }) => (
        <HfoOverlaySwitch
          subjectId={subjectId}
          edfArtifactId={edfArtifactId}
          checked={showOverlay}
          onChange={setShowOverlay}
        />
      )}
    >
      {(ctx) => <InterictalWorkspace subjectId={subjectId} showOverlay={showOverlay} {...ctx} />}
    </EdfWorkspace>
  );
}
