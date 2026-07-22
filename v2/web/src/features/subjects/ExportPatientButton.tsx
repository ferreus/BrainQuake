import { useCallback, useState } from "react";
import { Button } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useExportPatient } from "../../api/queries/usePatientIo";
import { useJobPolling } from "../../api/queries/useJobPolling";
import { patientExportDownloadUrl } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type { Job } from "../../api/types";

/**
 * "Download Patient": queues a server-side job that zips the subject's entire
 * on-disk footprint, watches that job to completion, then hands the browser
 * the finished archive. The job also shows up in the Jobs panel like any
 * other, so a page reload mid-export doesn't lose it.
 */
export function ExportPatientButton({ subjectId, subjectName }: { subjectId: number; subjectName: string }) {
  const exportPatient = useExportPatient();
  const [jobId, setJobId] = useState<number | undefined>();

  const triggerBrowserDownload = useCallback(() => {
    const a = document.createElement("a");
    a.href = patientExportDownloadUrl(subjectId);
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

  async function handleClick() {
    try {
      const job = await exportPatient.mutateAsync(subjectId);
      setJobId(job.id);
      notifications.show({
        color: "blue",
        title: "Preparing export",
        message: "Zipping patient data — the download starts automatically when it's ready.",
      });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      notifications.show({ color: "red", title: "Could not start export", message });
    }
  }

  const busy = exportPatient.isPending || jobId != null;

  return (
    <Button variant="default" size="xs" onClick={handleClick} loading={busy}>
      Download Patient
    </Button>
  );
}
