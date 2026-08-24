import {
  IconActivity,
  IconBrain,
  IconChartDots3,
  IconHeartRateMonitor,
  IconTargetArrow,
  IconWaveSine,
} from "@tabler/icons-react";

export const SUBJECT_VIEWS = [
  { value: "electrodes", label: "Electrodes", icon: IconChartDots3 },
  { value: "ictal", label: "Ictal", icon: IconActivity },
  { value: "interictal", label: "Interictal", icon: IconWaveSine },
  { value: "clinical", label: "Clinical EEG", icon: IconHeartRateMonitor },
  { value: "soz", label: "SOZ Result", icon: IconTargetArrow },
  { value: "freebrowse", label: "FreeBrowse", icon: IconBrain },
] as const;

export type SubjectView = (typeof SUBJECT_VIEWS)[number]["value"];
export const DEFAULT_VIEW: SubjectView = "electrodes";
