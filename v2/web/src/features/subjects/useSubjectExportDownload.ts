import { useCallback, useState } from "react";
import { notifications } from "@mantine/notifications";
import { useExportSubject } from "../../api/queries/useSubjectIo";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { subjectExportDownloadUrl } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { Job } from "../../api/types";

/**
 * Queues a server-side job that zips the subject's entire on-disk footprint,
 * watches it to completion, then hands the browser the finished archive. The
 * job also shows up in the Jobs panel, so a page reload mid-export doesn't
 * lose it.
 */
export function useSubjectExportDownload(subjectId: number, subjectName: string) {
  const exportSubject = useExportSubject();
  const [jobId, setJobId] = useState<number | undefined>();

  const triggerBrowserDownload = useCallback(() => {
    const a = document.createElement("a");
    a.href = subjectExportDownloadUrl(subjectId);
    a.download = `${subjectName}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, [subjectId, subjectName]);

  const onTerminal = useCallback(
    (job: Job) => {
      setJobId(undefined);
      if (job.state === "finished") {
        notifications.show({
          color: "green",
          title: "Export ready",
          message: `${subjectName}: your download should begin now.`,
        });
        triggerBrowserDownload();
      } else {
        notifications.show({
          color: "red",
          title: "Export failed",
          message: job.progress_message ?? "See the Jobs panel for details.",
        });
      }
    },
    [subjectName, triggerBrowserDownload],
  );

  useJobPolling(jobId, onTerminal);

  const start = useCallback(async () => {
    try {
      const job = await exportSubject.mutateAsync(subjectId);
      setJobId(job.id);
      notifications.show({
        color: "blue",
        title: "Preparing export",
        message: "Zipping subject data — the download starts automatically when it's ready.",
      });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      notifications.show({ color: "red", title: "Could not start export", message });
    }
  }, [exportSubject, subjectId]);

  return { start, busy: exportSubject.isPending || jobId != null };
}
