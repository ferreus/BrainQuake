import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ActionIcon, Button, Group, Menu, NavLink, ScrollArea, Stack, Text } from "@mantine/core";
import { IconDotsVertical, IconDownload, IconTrash } from "@tabler/icons-react";
import type { Subject } from "../../api/types";
import { useDeleteSubject, useSubjects } from "../../api/queries/useSubjects";
import { useSubjectExportDownload } from "./useSubjectExportDownload";
import { NewSubjectDialog } from "./NewSubjectDialog";
import { ImportSubjectDialog } from "./ImportSubjectDialog";

function SubjectRow({
  subject,
  active,
  onSelect,
  onDelete,
}: {
  subject: Subject;
  active: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  const download = useSubjectExportDownload(subject.id, subject.name);

  return (
    <NavLink
      label={subject.name}
      description={subject.recon_type ?? "no recon type set"}
      active={active}
      onClick={onSelect}
      rightSection={
        <Menu position="bottom-end" withinPortal>
          <Menu.Target>
            <ActionIcon
              size="sm"
              variant="subtle"
              color="gray"
              component="span"
              loading={download.busy}
              onClick={(e) => e.stopPropagation()}
              title="Subject actions"
            >
              <IconDotsVertical size={16} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item
              leftSection={<IconDownload size={14} />}
              disabled={download.busy}
              onClick={() => download.start()}
            >
              Download subject
            </Menu.Item>
            <Menu.Divider />
            <Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={onDelete}>
              Delete subject
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      }
    />
  );
}

export function SubjectList() {
  const { data: subjects, isLoading } = useSubjects();
  const deleteSubject = useDeleteSubject();
  const navigate = useNavigate();
  const { subjectId } = useParams();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  function handleDelete(subject: Subject) {
    if (!confirm(`Delete subject "${subject.name}"? This removes all associated data.`)) return;
    deleteSubject.mutate(subject.id);
    if (String(subject.id) === subjectId) {
      navigate("/subjects");
    }
  }

  return (
    <Stack h="100%" gap="sm">
      <Group justify="space-between" px="sm" pt="sm" wrap="nowrap">
        <Text fw={700} size="sm">
          Subjects
        </Text>
        <Group gap="xs" wrap="nowrap">
          <Button size="xs" variant="default" onClick={() => setImportOpen(true)}>
            Import
          </Button>
          <Button size="xs" onClick={() => setDialogOpen(true)}>
            New Subject
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
            No subjects yet.
          </Text>
        )}
        {subjects?.map((subject) => (
          <SubjectRow
            key={subject.id}
            subject={subject}
            active={String(subject.id) === subjectId}
            onSelect={() => navigate(`/subjects/${subject.id}`)}
            onDelete={() => handleDelete(subject)}
          />
        ))}
      </ScrollArea>

      <NewSubjectDialog
        opened={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onCreated={(id) => navigate(`/subjects/${id}`)}
      />

      <ImportSubjectDialog
        opened={importOpen}
        onClose={() => setImportOpen(false)}
        onImported={(id) => navigate(`/subjects/${id}`)}
      />
    </Stack>
  );
}
