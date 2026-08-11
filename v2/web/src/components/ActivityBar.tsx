import { ActionIcon, Divider, Stack, Tooltip } from "@mantine/core";
import { IconStack2, IconUsers } from "@tabler/icons-react";
import { useMatch, useNavigate } from "react-router-dom";
import { DEFAULT_VIEW, SUBJECT_VIEWS } from "./subjectViews";

interface ActivityBarProps {
  subjectsOpen: boolean;
  onToggleSubjects: () => void;
  jobsOpen: boolean;
  onToggleJobs: () => void;
}

/** VSCode-style icon rail: subjects-panel toggle on top, one icon per view,
 * jobs-panel toggle pinned to the bottom. Active view lives in the URL. */
export function ActivityBar({ subjectsOpen, onToggleSubjects, jobsOpen, onToggleJobs }: ActivityBarProps) {
  const match = useMatch("/subjects/:subjectId/:view?");
  const navigate = useNavigate();
  const subjectId = match?.params.subjectId;
  const activeView = subjectId ? (match?.params.view ?? DEFAULT_VIEW) : undefined;

  return (
    <Stack
      w={48}
      h="100%"
      py="xs"
      gap={4}
      align="center"
      style={{ borderRight: "1px solid var(--mantine-color-default-border)", flexShrink: 0 }}
    >
      <Tooltip label={subjectsOpen ? "Hide subjects panel" : "Show subjects panel"} position="right">
        <ActionIcon
          variant={subjectsOpen ? "light" : "subtle"}
          color={subjectsOpen ? "blue" : "gray"}
          size="lg"
          onClick={onToggleSubjects}
          aria-label="Toggle subjects panel"
        >
          <IconUsers size={20} />
        </ActionIcon>
      </Tooltip>

      <Divider w="60%" my={2} />

      {SUBJECT_VIEWS.map(({ value, label, icon: Icon }) => (
        <Tooltip key={value} label={label} position="right" disabled={!subjectId}>
          <ActionIcon
            variant={activeView === value ? "light" : "subtle"}
            color={activeView === value ? "blue" : "gray"}
            size="lg"
            disabled={!subjectId}
            onClick={() => subjectId && navigate(`/subjects/${subjectId}/${value}`)}
            aria-label={label}
          >
            <Icon size={20} />
          </ActionIcon>
        </Tooltip>
      ))}

      <div style={{ flex: 1 }} />

      <Tooltip label={jobsOpen ? "Hide jobs panel" : "Show jobs panel"} position="right">
        <ActionIcon
          variant={jobsOpen ? "light" : "subtle"}
          color={jobsOpen ? "blue" : "gray"}
          size="lg"
          onClick={onToggleJobs}
          aria-label="Toggle jobs panel"
        >
          <IconStack2 size={20} />
        </ActionIcon>
      </Tooltip>
    </Stack>
  );
}
