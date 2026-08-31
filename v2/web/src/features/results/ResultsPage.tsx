import { useEffect, useState } from "react";
import { Alert, Button, Group, NumberInput, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { interpolatePlasma } from "d3-scale-chromatic";
import { ApiError } from "../../api/client";
import { useAnalysisRuns, type AnalysisRun } from "../../api/queries/useAnalysis";
import { useArtifacts, useDeleteArtifact } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { useFuseSoz, useSozResult } from "../../api/queries/useSoz";
import { useSurfaceMesh } from "../../api/queries/useSurfaceMesh";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { SceneView } from "../../components/three/SceneView";
import { SozContacts } from "../../components/three/SozContacts";
import { ANALYSIS_PROCESSES } from "../analysis/processes";
import { RunPickerPanel } from "./RunPickerPanel";
import { SozResultTable } from "./SozResultTable";

interface ResultsPageProps {
  subjectId: number;
  /** Mount the WebGL canvas only while this view is visible -- see ElectrodesPage. */
  active: boolean;
}

const PROCESS_IDS = ANALYSIS_PROCESSES.map((p) => p.id);

/** Horizontal plasma gradient legend for the combined suspicion score
 * (0 = low, 1 = high), matching the mayavi colorbar in client_soz.py. */
function ScoreLegend() {
  const stops = Array.from({ length: 11 }, (_, i) => interpolatePlasma(i / 10)).join(", ");
  return (
    <Group gap={6} align="center" wrap="nowrap">
      <Text size="xs" c="dimmed">
        low
      </Text>
      <div style={{ flex: 1, height: 10, borderRadius: 3, background: `linear-gradient(to right, ${stops})` }} />
      <Text size="xs" c="dimmed">
        high
      </Text>
    </Group>
  );
}

export function ResultsPage({ subjectId, active }: ResultsPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const hasElectrodes = (artifacts ?? []).some((a) => a.kind === "chnXyzDict");
  const { runs } = useAnalysisRuns(subjectId, PROCESS_IDS);

  const [topN, setTopN] = useState(10);
  const [jobId, setJobId] = useState<number | undefined>();
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Everything is selected by default; unticking is how a bad run is excluded.
  const runsKey = runs.map((r) => r.artifactId).join(",");
  useEffect(() => {
    setSelected((prev) => {
      const live = new Set(runs.map((r) => r.artifactId));
      const kept = new Set([...prev].filter((id) => live.has(id)));
      return kept.size === prev.size && prev.size > 0 ? prev : live;
    });
  }, [runsKey]);

  const fuseSoz = useFuseSoz(subjectId);
  const deleteArtifact = useDeleteArtifact(subjectId);
  const queryClient = useQueryClient();
  const { data: result } = useSozResult(subjectId, true);
  const rows = result?.rows;

  const lhSurface = useSurfaceMesh(subjectId, "lh");
  const rhSurface = useSurfaceMesh(subjectId, "rh");
  const surfaceMissing = lhSurface.isError && rhSurface.isError;

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["soz-result", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "Fusion failed", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleFuse() {
    try {
      const j = await fuseSoz.mutateAsync({ artifact_ids: [...selected] });
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start fusion",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  async function handleDelete(run: AnalysisRun) {
    try {
      await deleteArtifact.mutateAsync(run.artifactId);
      queryClient.invalidateQueries({ queryKey: ["analysis-aggregate"] });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to delete result",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;
  const ready = hasElectrodes && selected.size > 0;

  return (
    <Group align="stretch" wrap="nowrap" gap="md" mt="md" style={{ flex: 1, minHeight: 0 }}>
      <div style={{ flex: 1, height: "100%", position: "relative" }}>
        {active && (
          <SceneView style={{ width: "100%", height: "100%" }}>
            <BrainMesh subjectId={subjectId} />
            {rows && rows.length > 0 && <SozContacts rows={rows} topN={topN} />}
          </SceneView>
        )}
        {surfaceMissing && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Text size="sm" c="dimmed">
              No cached brain surface yet — generate it on the Electrodes tab.
            </Text>
          </div>
        )}
      </div>

      <Stack w={460} h="100%" gap="md" style={{ overflowY: "auto" }}>
        <Paper withBorder p="sm">
          <Title order={6} mb="xs">
            Analysis runs
          </Title>
          <Text size="xs" c="dimmed" mb="xs">
            Each ticked run is rank-percentiled on its own, averaged with the other runs of its
            process, then averaged across processes — so five fragility runs do not outvote one EI.
          </Text>
          {!hasElectrodes && (
            <Alert color="gray" variant="light" mb="xs" p="xs">
              <Text size="xs">Needs segmented electrodes first — contacts have no coordinates yet.</Text>
            </Alert>
          )}
          <RunPickerPanel runs={runs} selected={selected} onChange={setSelected} onDelete={handleDelete} />
          <Group align="flex-end" gap="sm" mt="sm">
            <NumberInput
              label="Contacts to label in 3D"
              value={topN}
              onChange={(v) => setTopN(Math.max(0, Number(v) || 0))}
              size="xs"
              min={0}
              w={140}
            />
            <Button size="xs" loading={running} disabled={!ready} onClick={handleFuse}>
              Fuse {selected.size} run{selected.size === 1 ? "" : "s"}
            </Button>
          </Group>
          {job?.state === "running" && (
            <Text size="xs" c="dimmed" mt={4}>
              {job.progress_message}
            </Text>
          )}
          <Stack gap={2} mt="sm">
            <Text size="xs" c="dimmed">
              Suspicion score
            </Text>
            <ScoreLegend />
          </Stack>
        </Paper>

        <Paper withBorder p="sm" style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          <Title order={6} mb="xs">
            Ranked contacts {rows ? `(${rows.length})` : ""}
          </Title>
          {rows && rows.length > 0 ? (
            <div style={{ flex: 1, minHeight: 0 }}>
              <SozResultTable rows={rows} processes={result?.processes ?? []} />
            </div>
          ) : (
            <Text size="xs" c="dimmed">
              Not fused yet for this subject.
            </Text>
          )}
        </Paper>
      </Stack>
    </Group>
  );
}
