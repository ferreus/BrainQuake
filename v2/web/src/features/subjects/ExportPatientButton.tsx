import { Button } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useExportPatient } from "../../api/queries/usePatientIo";
import { useJobRunner } from "../../api/queries/useJobRunner";
import { patientExportDownloadUrl } from "../../api/endpoints";

/**
 * "Download Patient": queues a server-side job that zips the subject's entire
 * on-disk footprint, watches that job to completion, then hands the browser
 * the finished archive. The job also shows up in the Jobs panel like any
 * other, so a page reload mid-export doesn't lose it.
 */
export function ExportPatientButton({ subjectId, subjectName }: { subjectId: number; subjectName: string }) {
  const exportPatient = useExportPatient();

  const { run, running } = useJobRunner({
    start: () => exportPatient.mutateAsync(subjectId),
    failTitle: "Export failed",
    startFailTitle: "Could not start export",
    onTerminal: (job) => {
      if (job.state !== "finished") return;
      notifications.show({
        color: "green",
        title: "Export ready",
        message: `${subjectName}: your download should begin now.`,
      });
      const a = document.createElement("a");
      a.href = patientExportDownloadUrl(subjectId);
      a.download = `${subjectName}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
  });

  async function handleClick() {
    if (!(await run())) return;
    notifications.show({
      color: "blue",
      title: "Preparing export",
      message: "Zipping patient data — the download starts automatically when it's ready.",
    });
  }

  return (
    <Button variant="default" size="xs" onClick={handleClick} loading={running}>
      Download Patient
    </Button>
  );
}
