import { useState } from "react";
import { Button, FileButton, Group, Modal, NativeSelect, Progress, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useCreateSubject } from "../../api/queries/useSubjects";
import { useRunRecon } from "../../api/queries/useRecon";
import { uploadFileWithProgress } from "../../api/client";
import { ApiError } from "../../api/client";
import { RECON_TYPES } from "../../api/types";
import type { ReconType } from "../../api/types";

interface NewPatientDialogProps {
  opened: boolean;
  onClose: () => void;
  onCreated: (subjectId: number) => void;
}

type Stage = "idle" | "creating" | "uploading-t1" | "uploading-ct" | "starting-recon";

export function NewPatientDialog({ opened, onClose, onCreated }: NewPatientDialogProps) {
  const [name, setName] = useState("");
  const [reconType, setReconType] = useState<ReconType>("recon-all");
  const [t1File, setT1File] = useState<File | null>(null);
  const [ctFile, setCtFile] = useState<File | null>(null);
  const [stage, setStage] = useState<Stage>("idle");
  const [progress, setProgress] = useState(0);

  const createSubject = useCreateSubject();
  const runRecon = useRunRecon();

  const busy = stage !== "idle";

  function reset() {
    setName("");
    setReconType("recon-all");
    setT1File(null);
    setCtFile(null);
    setStage("idle");
    setProgress(0);
  }

  async function handleSubmit() {
    if (!name.trim() || !t1File) {
      notifications.show({
        color: "red",
        title: "Missing required fields",
        message: "A patient name and a T1 (MRI) file are both required.",
      });
      return;
    }

    try {
      setStage("creating");
      const subject = await createSubject.mutateAsync({ name: name.trim(), reconType });

      setStage("uploading-t1");
      setProgress(0);
      await uploadFileWithProgress(`/subjects/${subject.id}/upload`, t1File, "t1", setProgress).promise;

      if (ctFile) {
        setStage("uploading-ct");
        setProgress(0);
        await uploadFileWithProgress(`/subjects/${subject.id}/upload`, ctFile, "ct", setProgress).promise;
      }

      setStage("starting-recon");
      await runRecon.mutateAsync({ subjectId: subject.id, reconType });

      notifications.show({
        color: "green",
        title: "Reconstruction queued",
        message: `${name}: watch progress in the Jobs panel below.`,
      });
      onCreated(subject.id);
      reset();
      onClose();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      notifications.show({ color: "red", title: "Failed to create patient", message });
      setStage("idle");
    }
  }

  const stageLabel: Record<Stage, string> = {
    idle: "",
    creating: "Creating patient record...",
    "uploading-t1": "Uploading T1 (MRI)...",
    "uploading-ct": "Uploading CT...",
    "starting-recon": "Starting reconstruction...",
  };

  return (
    <Modal
      opened={opened}
      onClose={() => {
        if (!busy) {
          reset();
          onClose();
        }
      }}
      title="New Patient"
      closeOnClickOutside={!busy}
      withCloseButton={!busy}
    >
      <Stack>
        <TextInput
          label="Patient name"
          placeholder="e.g. S1"
          value={name}
          onChange={(e) => setName(e.currentTarget.value)}
          disabled={busy}
          required
        />
        <NativeSelect
          label="Reconstruction type"
          data={RECON_TYPES}
          value={reconType}
          onChange={(e) => setReconType(e.currentTarget.value as ReconType)}
          disabled={busy}
        />
        <div>
          <Text size="sm" fw={500} mb={4}>
            MRI (T1) <Text component="span" c="red">*</Text>
          </Text>
          <FileButton onChange={setT1File} accept=".nii.gz,.nii,application/gzip" disabled={busy}>
            {(props) => (
              <Button variant="default" {...props} disabled={busy}>
                {t1File ? t1File.name : "Choose T1 file"}
              </Button>
            )}
          </FileButton>
        </div>
        <div>
          <Text size="sm" fw={500} mb={4}>
            CT (optional)
          </Text>
          <FileButton onChange={setCtFile} accept=".nii.gz,.nii,application/gzip" disabled={busy}>
            {(props) => (
              <Button variant="default" {...props} disabled={busy}>
                {ctFile ? ctFile.name : "Choose CT file"}
              </Button>
            )}
          </FileButton>
        </div>

        {busy && (
          <Stack gap={4}>
            <Text size="sm" c="dimmed">
              {stageLabel[stage]}
            </Text>
            {(stage === "uploading-t1" || stage === "uploading-ct") && (
              <Progress value={progress * 100} animated />
            )}
          </Stack>
        )}

        <Group justify="flex-end">
          <Button variant="subtle" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} loading={busy}>
            Upload &amp; Reconstruct
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
