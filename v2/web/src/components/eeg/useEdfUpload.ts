import { useState } from "react";
import { notifications } from "@mantine/notifications";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../../api/client";
import { uploadEdf } from "../../api/endpoints";
import type { Artifact } from "../../api/types";
import { edfDisplayName } from "../../lib/edfName";

/** The one EDF import path, shared by the recording bar and the empty state.
 * Handles the overwrite prompt, the artifact-list invalidation ordering and
 * the progress value the callers render. */
export function useEdfUpload(
  subjectId: number,
  recordings: Artifact[],
  onUploaded: (edfArtifactId: number) => void,
) {
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const queryClient = useQueryClient();

  async function upload(file: File | null) {
    if (!file) return;
    // The server also refuses a duplicate name (409), but asking here means the
    // question is answered before uploading a multi-GB file, not after.
    const clash = recordings.find(
      (a) => edfDisplayName(a).toLowerCase() === file.name.replace(/\.edf$/i, "").toLowerCase(),
    );
    if (clash && !confirm(`"${edfDisplayName(clash)}" already exists. Overwrite it and its EI/HFO results?`)) {
      return;
    }

    setUploadProgress(0);
    try {
      const artifact = await uploadEdf(subjectId, file, { overwrite: !!clash }, setUploadProgress).promise;
      // Awaited so the new id is already in the list before it is selected.
      await queryClient.invalidateQueries({ queryKey: ["artifacts", subjectId] });
      if (clash) queryClient.invalidateQueries({ queryKey: ["jobs"] });
      onUploaded(artifact.id);
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Failed to upload EDF",
        message: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setUploadProgress(null);
    }
  }

  return { upload, uploadProgress };
}
