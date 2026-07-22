import { useState } from "react";
import { Alert, Button, FileButton, Group, Modal, Progress, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { importPatient } from "../../api/endpoints";
import { ApiError } from "../../api/client";

interface ImportPatientDialogProps {
  opened: boolean;
  onClose: () => void;
  onImported: (subjectId: number) => void;
}

/**
 * Uploads a previously exported patient zip. The server reads the subject name
 * from the archive manifest, creates the subject, and queues an import job that
 * unpacks the payload and re-registers artifacts. The subject appears in the
 * list immediately; the import job's progress shows in the Jobs panel.
 */
export function ImportPatientDialog({ opened, onClose, onImported }: ImportPatientDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const queryClient = useQueryClient();

  function reset() {
    setFile(null);
    setProgress(0);
    setBusy(false);
  }

  async function handleSubmit() {
    if (!file) return;
    try {
      setBusy(true);
      setProgress(0);
      const { subject } = await importPatient(file, setProgress).promise;

      queryClient.invalidateQueries({ queryKey: ["subjects"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      notifications.show({
        color: "green",
        title: "Import queued",
        message: `${subject.name}: unpacking on the server — watch the Jobs panel below.`,
      });
      onImported(subject.id);
      reset();
      onClose();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      notifications.show({ color: "red", title: "Import failed", message });
      setBusy(false);
    }
  }

  return (
    <Modal
      opened={opened}
      onClose={() => {
        if (!busy) {
          reset();
          onClose();
        }
      }}
      title="Import Patient"
      closeOnClickOutside={!busy}
      withCloseButton={!busy}
    >
      <Stack>
        <Alert color="gray" variant="light">
          Select a patient archive previously created with "Download Patient". The patient name is
          taken from the archive and must not already exist on this server.
        </Alert>

        <div>
          <Text size="sm" fw={500} mb={4}>
            Patient archive (.zip) <Text component="span" c="red">*</Text>
          </Text>
          <FileButton onChange={setFile} accept=".zip,application/zip" disabled={busy}>
            {(props) => (
              <Button variant="default" {...props} disabled={busy}>
                {file ? file.name : "Choose zip file"}
              </Button>
            )}
          </FileButton>
        </div>

        {busy && (
          <Stack gap={4}>
            <Text size="sm" c="dimmed">
              Uploading archive...
            </Text>
            <Progress value={progress * 100} animated />
          </Stack>
        )}

        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={busy} disabled={!file}>
            Upload &amp; Import
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
