import { useState } from "react";
import { ScrollArea, Stack, Table, Text } from "@mantine/core";
import { useJobs } from "../../api/queries/useJobs";
import { useSubjects } from "../../api/queries/useSubjects";
import { JobRow } from "./JobRow";
import { JobLogViewer } from "./JobLogViewer";

/** Always-mounted bottom panel listing every job, newest first (job table +
 * log tail). */
export function JobsDrawer() {
  const [viewingLogJobId, setViewingLogJobId] = useState<number | null>(null);
  const { data: jobs, isLoading } = useJobs();
  const { data: subjects } = useSubjects();

  const sorted = [...(jobs ?? [])].sort((a, b) => b.id - a.id);
  const subjectNames = new Map((subjects ?? []).map((s) => [s.id, s.name]));

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
                <Table.Th>Subject</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th>Progress</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {sorted.map((job) => (
                <JobRow
                  key={job.id}
                  job={job}
                  subjectName={subjectNames.get(job.subject_id)}
                  onViewLog={setViewingLogJobId}
                />
              ))}
            </Table.Tbody>
          </Table>
        )}
      </ScrollArea>
      <JobLogViewer jobId={viewingLogJobId} onClose={() => setViewingLogJobId(null)} />
    </Stack>
  );
}
