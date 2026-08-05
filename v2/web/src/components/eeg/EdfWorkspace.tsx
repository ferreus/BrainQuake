import { useState } from "react";
import type { Dispatch, ReactNode } from "react";
import { Button, FileButton, Group, Loader, NativeSelect, Progress, Stack, Text } from "@mantine/core";
import { useArtifacts, useDeleteArtifact } from "../../api/queries/useElectrodes";
import { useEdfMeta } from "../../api/queries/useEdf";
import { qk } from "../../api/queryKeys";
import { useQueryClient } from "@tanstack/react-query";
import { showApiError } from "../../lib/notify";
import { useFileUpload } from "../../lib/useFileUpload";
import { EdfLoadErrorPanel } from "./EdfLoadErrorPanel";
import { EegToolbar } from "./EegToolbar";
import { useEegViewerState } from "./useEegViewerState";
import type { EdfMeta } from "../../api/endpoints";
import type { EegMode, EegViewerAction, EegViewerState } from "./useEegViewerState";

/** What the shell hands its children once an EDF is loaded and parseable. */
export interface EdfWorkspaceContext {
  edfArtifactId: number;
  meta: EdfMeta;
  state: EegViewerState;
  dispatch: Dispatch<EegViewerAction>;
}

interface EdfWorkspaceProps {
  subjectId: number;
  mode: EegMode;
  /** Shown when the subject has no EDF recordings at all. */
  emptyText: string;
  /** Extra controls for the top bar, rendered only once an EDF is loaded. */
  toolbarExtra?: (ctx: EdfWorkspaceContext) => ReactNode;
  /** Fires when the selected recording changes, for state tied to the old one. */
  onEdfChanged?: () => void;
  children: (ctx: EdfWorkspaceContext) => ReactNode;
}

/**
 * The recording-picker shell shared by the Ictal and Interictal tabs: EDF
 * selection, import, viewer state, and the loading/error/empty states around
 * a recording that may not parse. Both tabs are the same workspace differing
 * only in which compute form and result panel hang off it, which is what the
 * `children` render prop supplies.
 */
export function EdfWorkspace({
  subjectId,
  mode,
  emptyText,
  toolbarExtra,
  onEdfChanged,
  children,
}: EdfWorkspaceProps) {
  const { data: artifacts } = useArtifacts(subjectId);
  const edfArtifacts = (artifacts ?? []).filter((a) => a.kind === "raw_edf");
  const [selectedEdfId, setSelectedEdfId] = useState<number | undefined>();
  const queryClient = useQueryClient();

  const effectiveEdfId = selectedEdfId ?? edfArtifacts[0]?.id;

  const { data: meta, isLoading, isError, error, refetch } = useEdfMeta(subjectId, effectiveEdfId);
  const { state, dispatch } = useEegViewerState(mode);
  const deleteArtifact = useDeleteArtifact(subjectId);
  const { progress, upload } = useFileUpload("Failed to upload EDF");

  function selectEdf(id: number | undefined) {
    setSelectedEdfId(id);
    onEdfChanged?.();
  }

  function handleRemoveBadEdf() {
    if (!effectiveEdfId) return;
    deleteArtifact.mutate(effectiveEdfId, {
      onSuccess: () => selectEdf(undefined),
      onError: (err) => showApiError("Failed to remove EDF", err),
    });
  }

  async function handleUpload(file: File | null) {
    if (!file) return;
    const artifact = await upload<{ id: number }>(`/subjects/${subjectId}/upload`, file, "edf");
    if (!artifact) return;
    queryClient.invalidateQueries({ queryKey: qk.artifacts(subjectId) });
    selectEdf(artifact.id);
  }

  const ctx = meta && effectiveEdfId ? { edfArtifactId: effectiveEdfId, meta, state, dispatch } : null;

  return (
    <Stack h="100%" gap="sm" mt="md">
      <Group align="flex-end" gap="md" wrap="wrap">
        <NativeSelect
          label="EDF recording"
          data={edfArtifacts.map((a) => ({
            value: String(a.id),
            label: (a.meta_json?.original_filename as string) ?? `#${a.id}`,
          }))}
          value={effectiveEdfId ? String(effectiveEdfId) : ""}
          onChange={(e) => selectEdf(Number(e.currentTarget.value))}
          disabled={edfArtifacts.length === 0}
        />
        <FileButton onChange={handleUpload} accept=".edf">
          {(props) => (
            <Button size="xs" variant="default" {...props}>
              Import .edf
            </Button>
          )}
        </FileButton>
        {ctx && <EegToolbar state={state} dispatch={dispatch} />}
        {ctx && toolbarExtra?.(ctx)}
      </Group>
      {progress != null && <Progress value={progress * 100} size="sm" animated />}

      {!effectiveEdfId && <Text c="dimmed">{emptyText}</Text>}

      {effectiveEdfId && isLoading && (
        <Group justify="center" p="xl">
          <Loader size="sm" />
        </Group>
      )}

      {effectiveEdfId && isError && (
        <EdfLoadErrorPanel
          title="Failed to load EDF recording"
          error={error}
          onRetry={() => refetch()}
          onRemove={handleRemoveBadEdf}
          removing={deleteArtifact.isPending}
        />
      )}

      {ctx && children(ctx)}
    </Stack>
  );
}
