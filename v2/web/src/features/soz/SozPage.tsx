import { useState } from "react";
import { Alert, Button, Group, NumberInput, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { interpolatePlasma } from "d3-scale-chromatic";
import { ApiError } from "../../api/client";
import { useArtifacts } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { useFuseSoz, useSozResult } from "../../api/queries/useSoz";
import { useSurfaceMesh } from "../../api/queries/useSurfaceMesh";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { SceneCanvas } from "../../components/three/SceneCanvas";
import { SozContacts } from "../../components/three/SozContacts";
import { SozResultTable } from "./SozResultTable";

interface SozPageProps {
  subjectId: number;
  /** Mount the WebGL canvas only while this view is visible -- see ElectrodesPage. */
  active: boolean;
}

/** Horizontal plasma gradient legend for the combined SOZ-suspicion score
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

export function SozPage({ subjectId, active }: SozPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const kinds = new Set((artifacts ?? []).map((a) => a.kind));
  const hasElectrodes = kinds.has("chnXyzDict");
  const hasEi = kinds.has("ei_npz");
  const hasHi = kinds.has("hfo_npz");
  const ready = hasElectrodes && hasEi && hasHi;

  const [topN, setTopN] = useState(10);
  const [jobId, setJobId] = useState<number | undefined>();

  const fuseSoz = useFuseSoz(subjectId);
  const queryClient = useQueryClient();
  const { data: rows } = useSozResult(subjectId, true);

  const lhSurface = useSurfaceMesh(subjectId, "lh");
  const rhSurface = useSurfaceMesh(subjectId, "rh");
  const surfaceMissing = lhSurface.isError && rhSurface.isError;

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["soz-result", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({ color: "red", title: "SOZ fusion failed", message: finishedJob.progress_message ?? "" });
    }
  });

  async function handleFuse() {
    try {
      const j = await fuseSoz.mutateAsync({});
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start SOZ fusion",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  const missing = [
    !hasElectrodes && "segmented electrodes",
    !hasEi && "an EI (ictal) result",
    !hasHi && "an HI (interictal HFO) result",
  ].filter(Boolean);

  return (
    <Group align="stretch" wrap="nowrap" gap="md" mt="md" style={{ flex: 1, minHeight: 0 }}>
      <div style={{ flex: 1, height: "100%", position: "relative" }}>
        {active && (
          <SceneCanvas>
            <BrainMesh subjectId={subjectId} />
            {rows && rows.length > 0 && <SozContacts rows={rows} topN={topN} />}
          </SceneCanvas>
        )}
        {surfaceMissing && (
          <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Text size="sm" c="dimmed">
              No cached brain surface yet — generate it on the Electrodes tab.
            </Text>
          </div>
        )}
      </div>

      <Stack w={420} h="100%" gap="md" style={{ overflowY: "auto" }}>
        <Paper withBorder p="sm">
          <Title order={6} mb="xs">
            Fuse EI + HI
          </Title>
          <Text size="xs" c="dimmed" mb="xs">
            Ranks every contact by combining its EI (ictal) and HI (interictal) percentiles into a single suspicion score.
          </Text>
          {!ready && (
            <Alert color="gray" variant="light" mb="xs" p="xs">
              <Text size="xs">Needs {missing.join(", ")} first. Run those tabs, then fuse.</Text>
            </Alert>
          )}
          <Group align="flex-end" gap="sm">
            <NumberInput
              label="Contacts to label in 3D"
              value={topN}
              onChange={(v) => setTopN(Math.max(0, Number(v) || 0))}
              size="xs"
              min={0}
              w={140}
            />
            <Button size="xs" loading={running} disabled={!ready} onClick={handleFuse}>
              {rows && rows.length > 0 ? "Re-fuse" : "Fuse EI + HI"}
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
              <SozResultTable rows={rows} />
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
