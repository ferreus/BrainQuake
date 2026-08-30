import {
  IconActivity,
  IconBrain,
  IconChartDots3,
  IconHeartRateMonitor,
  IconTargetArrow,
} from "@tabler/icons-react";

export const SUBJECT_VIEWS = [
  { value: "electrodes", label: "Electrodes", icon: IconChartDots3 },
  { value: "analysis", label: "Analysis", icon: IconActivity },
  { value: "clinical", label: "Clinical EEG", icon: IconHeartRateMonitor },
  { value: "soz", label: "SOZ Result", icon: IconTargetArrow },
  { value: "freebrowse", label: "FreeBrowse", icon: IconBrain },
] as const;

export type SubjectView = (typeof SUBJECT_VIEWS)[number]["value"];
export const DEFAULT_VIEW: SubjectView = "electrodes";
