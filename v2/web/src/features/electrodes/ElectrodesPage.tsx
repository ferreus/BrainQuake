import { useState } from "react";
import { Button, Group, Paper, Stack, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { useArtifacts, useRegisterCt } from "../../api/queries/useElectrodes";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { TERMINAL_JOB_STATES } from "../../api/types";
import { BrainMesh } from "../../components/three/BrainMesh";
import { ElectrodeContacts } from "../../components/three/ElectrodeContacts";
import { SceneCanvas } from "../../components/three/SceneCanvas";
import { DetectForm } from "./DetectForm";
import { LabelReviewPanel } from "./LabelReviewPanel";
import { SegmentForm } from "./SegmentForm";

interface RegisterCtStepProps {
  subjectId: number;
  hasRawCt: boolean;
  ctRegistered: boolean;
}

function RegisterCtStep({ subjectId, hasRawCt, ctRegistered }: RegisterCtStepProps) {
  const [jobId, setJobId] = useState<number | undefined>();
  const registerCt = useRegisterCt(subjectId);
  const queryClient = useQueryClient();

  const { data: job } = useJobPolling(jobId, (finishedJob) => {
    queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
    if (finishedJob.state === "failed") {
      notifications.show({
        color: "red",
        title: "CT registration failed",
        message: finishedJob.progress_message ?? "",
      });
    }
  });

  async function handleRegister() {
    try {
      const j = await registerCt.mutateAsync();
      setJobId(j.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to start CT registration",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const running = job ? !TERMINAL_JOB_STATES.has(job.state) : false;

  return (
    <Paper withBorder p="sm">
      <Title order={6} mb="xs">
        0. Register CT
      </Title>
      <Group justify="space-between">
        <Text size="xs" c="dimmed">
          {ctRegistered
            ? "CT registered to MRI space"
            : hasRawCt
              ? "CT uploaded, not yet registered"
              : "Upload a CT scan for this patient first"}
        </Text>
        <Button size="xs" loading={running} disabled={!hasRawCt} onClick={handleRegister}>
          {ctRegistered ? "Re-register" : "Register"}
        </Button>
      </Group>
      {job?.state === "running" && (
        <Text size="xs" c="dimmed" mt={4}>
          {job.progress_message}
        </Text>
      )}
    </Paper>
  );
}

interface ElectrodesPageProps {
  subjectId: number;
}

export function ElectrodesPage({ subjectId }: ElectrodesPageProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const kinds = new Set((artifacts ?? []).map((a) => a.kind));
  const hasRawCt = kinds.has("raw_ct");
  const ctRegistered = kinds.has("ct_reg_nii");
  const detected = kinds.has("labels_npy");
  const segmented = kinds.has("chnXyzDict");

  return (
    <Group align="stretch" wrap="nowrap" gap="md" mt="md" h="100%">
      <div style={{ flex: 1, minHeight: 520 }}>
        <SceneCanvas>
          <BrainMesh subjectId={subjectId} />
          {segmented && <ElectrodeContacts subjectId={subjectId} />}
        </SceneCanvas>
      </div>
      <Stack w={360} gap="md">
        <RegisterCtStep subjectId={subjectId} hasRawCt={hasRawCt} ctRegistered={ctRegistered} />
        <DetectForm subjectId={subjectId} disabled={!ctRegistered} detected={detected} />
        {detected && <LabelReviewPanel subjectId={subjectId} />}
        <SegmentForm subjectId={subjectId} disabled={!detected} segmented={segmented} />
      </Stack>
    </Group>
  );
}
