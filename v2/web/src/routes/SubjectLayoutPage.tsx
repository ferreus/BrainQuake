import { useParams } from "react-router-dom";
import { Alert, Group, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { getSubject } from "../api/endpoints";
import { qk } from "../api/queryKeys";
import { ElectrodesPage } from "../features/electrodes/ElectrodesPage";
import { IctalPage } from "../features/ictal/IctalPage";
import { InterictalPage } from "../features/interictal/InterictalPage";
import { SozPage } from "../features/soz/SozPage";
import { ExportPatientButton } from "../features/subjects/ExportPatientButton";

export function SubjectLayoutPage() {
  const { subjectId } = useParams();
  const id = Number(subjectId);

  const { data: subject, isLoading, isError } = useQuery({
    queryKey: qk.subject(id),
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
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={2}>
          <Title order={3}>{subject.name}</Title>
          <Text size="sm" c="dimmed">
            Reconstruction type: {subject.recon_type ?? "not set"}
            {subject.subject_dir ? ` — ${subject.subject_dir}` : " — reconstruction not yet run"}
          </Text>
        </Stack>
        <ExportPatientButton key={id} subjectId={id} subjectName={subject.name} />
      </Group>

      {/* key={id} on every per-subject child: switching patients in the sidebar
          re-renders this route with a new :subjectId but does NOT remount it,
          so without a key the pages keep the previous patient's local state --
          most visibly the job ids the step forms poll on, which made e.g. the
          Register CT button sit in its loading state on a patient with no
          ct_register job of its own. */}
      <Tabs defaultValue="electrodes" style={{ flex: 1 }}>
        <Tabs.List>
          <Tabs.Tab value="electrodes">Electrodes</Tabs.Tab>
          <Tabs.Tab value="ictal">Ictal</Tabs.Tab>
          <Tabs.Tab value="interictal">Interictal</Tabs.Tab>
          <Tabs.Tab value="soz">SOZ Result</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="electrodes" style={{ height: "calc(100% - 40px)" }}>
          {subject.subject_dir ? (
            <ElectrodesPage key={id} subjectId={id} />
          ) : (
            <Alert color="gray" variant="light" mt="md">
              Run reconstruction first (see the patient's upload step) before working on electrodes.
            </Alert>
          )}
        </Tabs.Panel>
        <Tabs.Panel value="ictal" style={{ height: "calc(100% - 40px)" }}>
          <IctalPage key={id} subjectId={id} />
        </Tabs.Panel>
        <Tabs.Panel value="interictal" style={{ height: "calc(100% - 40px)" }}>
          <InterictalPage key={id} subjectId={id} />
        </Tabs.Panel>
        <Tabs.Panel value="soz" style={{ height: "calc(100% - 40px)" }}>
          <SozPage key={id} subjectId={id} />
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
