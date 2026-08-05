import { useCallback, useState } from "react";
import { uploadFileWithProgress } from "../api/client";
import { showApiError } from "./notify";

/**
 * Multipart upload with a progress fraction for the caller to render, plus
 * the try/catch/reset bookkeeping every upload site was repeating. Resolves
 * with the created artifact, or `undefined` if the upload failed (the error
 * has already been surfaced as a toast by then).
 */
export function useFileUpload(failTitle: string) {
  const [progress, setProgress] = useState<number | null>(null);

  const upload = useCallback(
    async <T,>(path: string, file: File, fileType: Parameters<typeof uploadFileWithProgress>[2]) => {
      setProgress(0);
      try {
        return await uploadFileWithProgress<T>(path, file, fileType, setProgress).promise;
      } catch (err) {
        showApiError(failTitle, err);
        return undefined;
      } finally {
        setProgress(null);
      }
    },
    [failTitle],
  );

  return { progress, uploading: progress != null, upload };
}
