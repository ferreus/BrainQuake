import { useState } from "react";
import { Collapse, ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";
import type { RecordingAnnotation } from "../../api/endpoints";

interface AnnotationsPanelProps {
  annotations: RecordingAnnotation[];
  /** Jumps/centers the EEG canvas on an annotation's onset time. */
  onJumpTo: (onset: number) => void;
}

function formatSeconds(s: number): string {
  return `${s.toFixed(1)}s`;
}

/** EDF+ annotations (clinical markings, seizure events) parsed at upload
 * time -- see docs/bella_ictal_ei_vs_annotation_discrepancy.md for why these
 * are just listed, not used to auto-fill baseline/target: picking a single
 * "the" onset from a cluster of nearby markings is not this component's call
 * to make. Collapsed by default to keep the canvas the main use of the
 * space; clicking a row jumps the trace view to that time. */
export function AnnotationsPanel({ annotations, onJumpTo }: AnnotationsPanelProps) {
  const [opened, setOpened] = useState(false);

  if (annotations.length === 0) return null;

  return (
    <Stack gap={4}>
      <UnstyledButton onClick={() => setOpened((o) => !o)}>
        <Text size="xs" fw={500}>
          {opened ? "▼" : "▶"} Annotations ({annotations.length})
        </Text>
      </UnstyledButton>
      <Collapse expanded={opened}>
        <ScrollArea h={140}>
          <Stack gap={2}>
            {annotations.map((a, i) => (
              <UnstyledButton key={i} onClick={() => onJumpTo(a.onset)}>
                <Text size="xs">
                  <Text span c="dimmed" ff="monospace">
                    {formatSeconds(a.onset)}
                  </Text>{" "}
                  {a.description}
                </Text>
              </UnstyledButton>
            ))}
          </Stack>
        </ScrollArea>
      </Collapse>
    </Stack>
  );
}
