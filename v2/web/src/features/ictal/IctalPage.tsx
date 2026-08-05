import { Group, Stack } from "@mantine/core";
import { EdfWorkspace } from "../../components/eeg/EdfWorkspace";
import { EegCanvas } from "../../components/eeg/EegCanvas";
import { EegChannelList } from "../../components/eeg/EegChannelList";
import { useBaselineTargetSelection } from "../../components/eeg/useBaselineTargetSelection";
import { EiComputeForm } from "./EiComputeForm";
import { EiResultPanel } from "./EiResultPanel";

interface IctalPageProps {
  subjectId: number;
}

export function IctalPage({ subjectId }: IctalPageProps) {
  const selection = useBaselineTargetSelection();

  const markers = [
    ...(selection.baselineRange
      ? [
          { time: selection.baselineRange[0], color: "#1baf7a" },
          { time: selection.baselineRange[1], color: "#1baf7a" },
        ]
      : []),
    ...(selection.targetRange
      ? [
          { time: selection.targetRange[0], color: "#eb6834" },
          { time: selection.targetRange[1], color: "#eb6834" },
        ]
      : []),
    // Draw the first click's line right away, before the closing click lands.
    ...(selection.pendingStart != null
      ? [{ time: selection.pendingStart, color: selection.awaitingClick?.startsWith("baseline") ? "#1baf7a" : "#eb6834" }]
      : []),
  ];

  return (
    <EdfWorkspace
      subjectId={subjectId}
      mode="ictal"
      emptyText="Import an ictal EDF recording to get started."
      onEdfChanged={selection.reset}
    >
      {({ edfArtifactId, meta, state, dispatch }) => (
        <>
          <Group align="flex-start" gap="md" wrap="nowrap">
            <EegCanvas
              subjectId={subjectId}
              edfArtifactId={edfArtifactId}
              state={state}
              dispatch={dispatch}
              markers={markers}
              onCanvasClick={selection.awaitingClick ? selection.handleClick : undefined}
            />
            <Stack w={300} gap="sm">
              <EegChannelList
                channels={meta.channels}
                excludedChannels={state.excludedChannels}
                onDelete={(chs) => dispatch({ type: "DELETE_CHANNELS", channels: chs })}
              />
              <EiComputeForm subjectId={subjectId} edfArtifactId={edfArtifactId} selection={selection} />
            </Stack>
          </Group>
          <EiResultPanel
            subjectId={subjectId}
            edfArtifactId={edfArtifactId}
            targetRange={selection.targetRange}
            bandLow={state.filterBandLow}
            bandHigh={state.filterBandHigh}
          />
        </>
      )}
    </EdfWorkspace>
  );
}
