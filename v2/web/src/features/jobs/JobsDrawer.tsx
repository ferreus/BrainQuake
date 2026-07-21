import { useState } from "react";
import { ScrollArea, Stack, Table, Text } from "@mantine/core";
import { useJobs } from "../../api/queries/useJobs";
import { JobRow } from "./JobRow";
import { JobLogViewer } from "./JobLogViewer";

/** Always-mounted bottom panel listing every job, newest first -- mirrors
 * v2/client/jobs_panel.py's dockable Jobs panel (job table + log tail). */
export function JobsDrawer() {
  const [viewingLogJobId, setViewingLogJobId] = useState<number | null>(null);
  const { data: jobs, isLoading } = useJobs();

  const sorted = [...(jobs ?? [])].sort((a, b) => b.id - a.id);

  return (
    <Stack gap={0} h="100%">
      <ScrollArea style={{ flex: 1 }}>
        {isLoading && (
          <Text p="sm" c="dimmed" size="sm">
            Loading jobs...
          </Text>
        )}
        {!isLoading && sorted.length === 0 && (
          <Text p="sm" c="dimmed" size="sm">
            No jobs yet.
          </Text>
        )}
        {sorted.length > 0 && (
          <Table stickyHeader striped verticalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>ID</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th>Progress</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sorted.map((job) => (
                <JobRow key={job.id} job={job} onViewLog={setViewingLogJobId} />
              ))}
            </Table.Tbody>
          </Table>
        )}
      </ScrollArea>
      <JobLogViewer jobId={viewingLogJobId} onClose={() => setViewingLogJobId(null)} />
    </Stack>
  );
}
