import type { ProcessPaneProps } from "./processes";
import { EiComputeForm } from "./EiComputeForm";
import { EiResultPanel } from "./EiResultPanel";
import { HfoComputeForm } from "./HfoComputeForm";
import { FragilityComputeForm } from "./FragilityComputeForm";
import { FragilityResultPanel } from "./FragilityResultPanel";
import { HiResultPanel } from "./HiResultPanel";

// Adapters, so the compute forms themselves move unedited: their params are what
// produced every existing result and must not shift.
export function EiParams(p: ProcessPaneProps) {
  return (
    <EiComputeForm
      subjectId={p.subjectId}
      edfArtifactId={p.edfArtifactId}
      selection={p.selection}
      sfreq={p.meta.fs}
      remainChannels={p.remainChannels}
      mainsFreq={p.state.mainsFreq}
      initialParams={p.recordingParams?.ictal_params ?? null}
    />
  );
}

export function HfoParams(p: ProcessPaneProps) {
  return (
    <HfoComputeForm
      subjectId={p.subjectId}
      edfArtifactId={p.edfArtifactId}
      bandLow={p.state.filterBandLow}
      bandHigh={p.state.filterBandHigh}
      remainChannels={p.remainChannels}
      sfreq={p.meta.fs}
      durationSec={p.meta.duration_sec}
      initialParams={p.recordingParams?.interictal_params ?? null}
    />
  );
}

export function EiResult(p: ProcessPaneProps) {
  return <EiResultPanel subjectId={p.subjectId} edfArtifactId={p.edfArtifactId} />;
}

export function HfoResult(p: ProcessPaneProps) {
  return <HiResultPanel subjectId={p.subjectId} edfArtifactId={p.edfArtifactId} />;
}


export function FragilityParams(p: ProcessPaneProps) {
  return <FragilityComputeForm {...p} />;
}

export function FragilityResult(p: ProcessPaneProps) {
  return <FragilityResultPanel {...p} />;
}
