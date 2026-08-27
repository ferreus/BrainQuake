import { butterLowpassSos, filtfilt, iirnotch, lfilter, lfilterZi, sosfiltfilt } from "../src/lib/dsp";
import { filterForReview } from "../src/features/clinical/reviewFilter";

const fs = 2000, secs = 71, nch = 20;
const n = fs * secs;
const make = () => {
  const x = new Float64Array(n);
  for (let i = 0; i < n; i++) x[i] = Math.sin(i / 20) + 0.3 * Math.sin(i / 3) + 1e-3 * i;
  return x;
};
const t = (label: string, fn: () => void) => {
  const t0 = performance.now();
  fn();
  console.log(`${label.padEnd(28)} ${(performance.now() - t0).toFixed(0)} ms`);
};

const one = make();
const { b, a } = iirnotch(50 / (fs / 2), 30);
const sos = butterLowpassSos(4, 70 / (fs / 2));
const rcB = [0.9, -0.9], rcA = [1, -0.9];

console.log(`${nch} ch x ${secs}s @ ${fs}Hz = ${(n * nch / 1e6).toFixed(1)}M samples\n`);
t("1x filtfilt (notch)", () => filtfilt(b, a, one));
t("1x lfilter (rc)", () => lfilter(rcB, rcA, one, lfilterZi(rcB, rcA)));
t("1x sosfiltfilt (butter4)", () => sosfiltfilt(sos, one));
console.log();
const rows = Array.from({ length: nch }, make);
t("full chain, 20ch, car", () =>
  filterForReview(rows, fs, { tc: 0.1, hicut: 70, mainsFreq: 50, reference: "car" }));
