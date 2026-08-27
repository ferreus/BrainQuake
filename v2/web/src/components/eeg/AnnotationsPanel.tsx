import { ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";
import type { RecordingAnnotation } from "../../api/endpoints";
import { formatClock } from "../../lib/eegTime";
import { CollapsibleSection } from "../CollapsibleSection";

interface AnnotationListProps {
  annotations: RecordingAnnotation[];
  /** Jumps/centers the EEG canvas on an annotation's onset time. */
  onJumpTo: (onset: number) => void;
  /** Seconds since midnight of the recording start; adds a wall-clock column. */
  clockOrigin?: number | null;
  /** [start, end] of the window on screen -- those rows are marked. */
  highlightRange?: [number, number];
}

function formatSeconds(s: number): string {
  return `${s.toFixed(1)}s`;
}

/** EDF+ annotations (clinical markings, seizure events) parsed at upload
 * time -- see docs/bella_ictal_ei_vs_annotation_discrepancy.md for why these
 * are just listed, not used to auto-fill baseline/target: picking a single
 * "the" onset from a cluster of nearby markings is not this component's call
 * to make. Clicking a row jumps the trace view to that time. */
export function AnnotationList({ annotations, onJumpTo, clockOrigin, highlightRange }: AnnotationListProps) {
  if (annotations.length === 0) {
    return <Text size="xs" c="dimmed">No annotations in this recording.</Text>;
  }

  return (
    <ScrollArea h={160}>
      <Stack gap={2}>
        {annotations.map((a, i) => {
          const onScreen = highlightRange != null && a.onset >= highlightRange[0] && a.onset <= highlightRange[1];
          return (
            <UnstyledButton
              key={i}
              onClick={() => onJumpTo(a.onset)}
              style={onScreen ? { background: "var(--mantine-color-red-light)", borderRadius: 3 } : undefined}
            >
              <Text size="xs">
                <Text span c="dimmed" ff="monospace">
                  {clockOrigin == null ? formatSeconds(a.onset) : formatClock(clockOrigin + a.onset)}
                </Text>{" "}
                {a.description}
              </Text>
            </UnstyledButton>
          );
        })}
      </Stack>
    </ScrollArea>
  );
}

/** Collapsed-by-default wrapper used by the analysis viewers, which keep the
 * canvas as the main use of the space and hide the section entirely when the
 * recording carries no markings. */
export function AnnotationsPanel({ annotations, onJumpTo }: AnnotationListProps) {
  if (annotations.length === 0) return null;

  return (
    <CollapsibleSection title="Annotations" badge={annotations.length} defaultOpened={false}>
      <AnnotationList annotations={annotations} onJumpTo={onJumpTo} />
    </CollapsibleSection>
  );
}
