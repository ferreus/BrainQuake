import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ActionIcon, Button, Group, NavLink, ScrollArea, Stack, Text } from "@mantine/core";
import { useDeleteSubject, useSubjects } from "../../api/queries/useSubjects";
import { NewPatientDialog } from "./NewPatientDialog";
import { ImportPatientDialog } from "./ImportPatientDialog";

export function SubjectList() {
  const { data: subjects, isLoading } = useSubjects();
  const deleteSubject = useDeleteSubject();
  const navigate = useNavigate();
  const { subjectId } = useParams();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  return (
    <Stack h="100%" gap="sm">
      <Group justify="space-between" px="sm" pt="sm" wrap="nowrap">
        <Text fw={700} size="sm">
          Patients
        </Text>
        <Group gap="xs" wrap="nowrap">
          <Button size="xs" variant="default" onClick={() => setImportOpen(true)}>
            Import
          </Button>
          <Button size="xs" onClick={() => setDialogOpen(true)}>
            New Patient
          </Button>
        </Group>
      </Group>

      <ScrollArea style={{ flex: 1 }} px={4}>
        {isLoading && (
          <Text size="sm" c="dimmed" px="sm">
            Loading...
          </Text>
        )}
        {!isLoading && subjects?.length === 0 && (
          <Text size="sm" c="dimmed" px="sm">
            No patients yet.
          </Text>
        )}
        {subjects?.map((subject) => (
          <NavLink
            key={subject.id}
            label={subject.name}
            description={subject.recon_type ?? "no recon type set"}
            active={String(subject.id) === subjectId}
            onClick={() => navigate(`/subjects/${subject.id}`)}
            rightSection={
              <ActionIcon
                size="sm"
                color="red"
                variant="subtle"
                component="span"
                onClick={(e) => {
                  e.stopPropagation();
                  if (confirm(`Delete patient "${subject.name}"? This removes all associated data.`)) {
                    deleteSubject.mutate(subject.id);
                    if (String(subject.id) === subjectId) {
                      navigate("/subjects");
                    }
                  }
                }}
                title="Delete patient"
              >
                ✕
              </ActionIcon>
            }
          />
        ))}
      </ScrollArea>

      <NewPatientDialog
        opened={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={(id) => navigate(`/subjects/${id}`)}
      />

      <ImportPatientDialog
        opened={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(id) => navigate(`/subjects/${id}`)}
      />
    </Stack>
  );
}
