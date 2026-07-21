import { useParams } from "react-router-dom";
import { Alert, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { getSubject } from "../api/endpoints";
import { ElectrodesPage } from "../features/electrodes/ElectrodesPage";
import { IctalPage } from "../features/ictal/IctalPage";

/** Placeholder for a not-yet-built stage tab -- each becomes a real feature
 * in its own phase (Phase 2: Electrodes, Phase 3: Ictal, Phase 4: Interictal,
 * Phase 5: SOZ), per the phased plan. */
function ComingSoon({ phase }: { phase: string }) {
  return (
    <Alert color="gray" variant="light" mt="md">
      Not built yet -- planned for {phase}.
    </Alert>
  );
}

export function SubjectLayoutPage() {
  const { subjectId } = useParams();
  const id = Number(subjectId);

  const { data: subject, isLoading, isError } = useQuery({
    queryKey: ["subject", id],
    queryFn: () => getSubject(id),
    enabled: Number.isFinite(id),
  });

  if (isLoading) {
    return (
      <Stack align="center" justify="center" h="100%">
        <Loader />
      </Stack>
    );
  }

  if (isError || !subject) {
    return (
      <Stack align="center" justify="center" h="100%">
        <Text c="red">Patient not found.</Text>
      </Stack>
    );
  }

  return (
    <Stack h="100%" p="md" gap="sm">
      <Title order={3}>{subject.name}</Title>
      <Text size="sm" c="dimmed">
        Reconstruction type: {subject.recon_type ?? "not set"}
        {subject.subject_dir ? ` — ${subject.subject_dir}` : " — reconstruction not yet run"}
      </Text>

      <Tabs defaultValue="electrodes" style={{ flex: 1 }}>
        <Tabs.List>
          <Tabs.Tab value="electrodes">Electrodes</Tabs.Tab>
          <Tabs.Tab value="ictal">Ictal</Tabs.Tab>
          <Tabs.Tab value="interictal">Interictal</Tabs.Tab>
          <Tabs.Tab value="soz">SOZ Result</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="electrodes" style={{ height: "calc(100% - 40px)" }}>
          {subject.subject_dir ? (
            <ElectrodesPage subjectId={id} />
          ) : (
            <Alert color="gray" variant="light" mt="md">
              Run reconstruction first (see the patient's upload step) before working on electrodes.
            </Alert>
          )}
        </Tabs.Panel>
        <Tabs.Panel value="ictal" style={{ height: "calc(100% - 40px)" }}>
          <IctalPage subjectId={id} />
        </Tabs.Panel>
        <Tabs.Panel value="interictal">
          <ComingSoon phase="Phase 4 (Interictal HFO)" />
        </Tabs.Panel>
        <Tabs.Panel value="soz">
          <ComingSoon phase="Phase 5 (SOZ fusion + 3D overlay)" />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
