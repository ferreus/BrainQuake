/** Diffs the client-side review filter against the server's.
 *
 * features/clinical/reviewFilter.ts is a second implementation of
 * app/sigproc/filters.py's filter_for_review, and a second implementation that
 * nobody checks is a second implementation that drifts. This fetches, for a
 * matrix of review settings, both the server-filtered window and the raw padded
 * window the server would have filtered, runs the TS chain over the raw one,
 * and reports the residual.
 *
 * filters.py is itself pinned to v2/tools/show_edf.py by pytest, so agreeing
 * with it is agreeing with the reference tool.
 *
 *   cd v2/web && npm run verify:filter -- http://localhost:8000 1 3
 */
import { parseEdfWindowBinary, type ParsedEdfWindow } from "../src/lib/parseEdfWindowBinary";
import { filterForReview, type ReviewReference } from "../src/features/clinical/reviewFilter";

// app/services/edf.py's own pad rule -- matched exactly so both sides filter
// byte-identical input and any residual is the filter, not the framing.
const PAD = 2.0;
const TC_SETTLE_TAUS = 5.0;
const MAX_PAD_SECONDS = 30.0;

const START = 5.0;
const SPAN = 10.0;
/** Above this the port is wrong. float32 on the wire vs float64 in MNE leaves
 * a residual around 1e-7 relative; anything near 1e-4 is a real divergence. */
const TOLERANCE = 1e-4;

const [baseUrl = "http://localhost:8000", subjectId = "1", edfId = "3"] = process.argv.slice(2);

interface WindowRequest {
  start: number;
  end: number;
  channels?: string[];
  tc?: number;
  bandHigh?: number;
  mainsFreq?: number;
  reference?: string;
}

async function getWindow(p: WindowRequest): Promise<ParsedEdfWindow> {
  const qs = new URLSearchParams({ start: String(p.start), end: String(p.end) });
  if (p.channels?.length) qs.set("channels", p.channels.join(","));
  if (p.tc != null) qs.set("tc", String(p.tc));
  if (p.bandHigh != null) qs.set("band_high", String(p.bandHigh));
  if (p.mainsFreq != null) qs.set("mains_freq", String(p.mainsFreq));
  if (p.reference) qs.set("reference", p.reference);
  const url = `${baseUrl}/subjects/${subjectId}/edf/${edfId}/window?${qs}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()} for ${url}`);
  return parseEdfWindowBinary(await res.arrayBuffer());
}

/** max|client - server| as a fraction of the server signal's RMS. */
function residual(client: Float64Array[], server: Float32Array[]): number {
  let maxDiff = 0;
  let sumSq = 0;
  let n = 0;
  for (let c = 0; c < server.length; c++) {
    for (let i = 0; i < server[c].length; i++) {
      maxDiff = Math.max(maxDiff, Math.abs(client[c][i] - server[c][i]));
      sumSq += server[c][i] * server[c][i];
      n++;
    }
  }
  const rms = Math.sqrt(sumSq / n);
  return rms === 0 ? maxDiff : maxDiff / rms;
}

interface Case {
  label: string;
  serverTc: number;
  bandHigh?: number;
  mainsFreq: number;
  reference: ReviewReference;
  channels: string[];
  /** What the raw buffer request sends; "bipolar" makes the server build
   * derivations, which the client never does itself. */
  rawReference: string;
}

async function run(c: Case): Promise<{ label: string; residual: number }> {
  const padLeft = Math.min(MAX_PAD_SECONDS, Math.max(PAD, TC_SETTLE_TAUS * c.serverTc));
  const raw = await getWindow({
    start: Math.max(0, START - padLeft),
    end: START + SPAN + PAD,
    channels: c.channels,
    reference: c.rawReference,
  });
  const server = await getWindow({
    start: START,
    end: START + SPAN,
    channels: c.channels,
    tc: c.serverTc,
    bandHigh: c.bandHigh,
    mainsFreq: c.mainsFreq,
    reference: c.rawReference === "bipolar" ? "bipolar" : c.reference,
  });

  const filtered = filterForReview(
    raw.data.map((d) => Float64Array.from(d)),
    raw.fs,
    {
      tc: c.serverTc || null,
      hicut: c.bandHigh ?? null,
      mainsFreq: c.mainsFreq,
      // Bipolar arrives already referenced; CAR must not go on top of it.
      reference: c.rawReference === "bipolar" ? "none" : c.reference,
    },
  );

  const trim0 = Math.round((START - raw.start) * raw.fs);
  const sliced = filtered.map((r) => r.slice(trim0, trim0 + server.data[0].length));
  return { label: c.label, residual: residual(sliced, server.data) };
}

const meta = await (await fetch(`${baseUrl}/subjects/${subjectId}/edf/${edfId}/meta`)).json();
const contacts: string[] = meta.channels.filter((c: string) => !(meta.aux_channels ?? []).includes(c)).slice(0, 20);
console.log(`fs=${meta.fs} Hz, ${contacts.length} channels, window ${START}-${START + SPAN}s\n`);

const cases: Case[] = [];
for (const serverTc of [0, 0.003, 0.1, 2]) {
  for (const bandHigh of [undefined, 15, 70, 300]) {
    for (const reference of ["none", "car"] as ReviewReference[]) {
      for (const mainsFreq of [50, 60]) {
        cases.push({
          label: `tc=${serverTc || "off"} hc=${bandHigh ?? "off"} ref=${reference} mains=${mainsFreq}`,
          serverTc,
          bandHigh,
          mainsFreq,
          reference,
          channels: contacts,
          rawReference: "none",
        });
      }
    }
  }
}

// One bipolar case: the pairing stays server-side, so all the client has to get
// right is *not* re-referencing on top of it.
const previewQs = new URLSearchParams();
for (const c of contacts) previewQs.append("remain_chns", c);
const preview = await (
  await fetch(`${baseUrl}/subjects/${subjectId}/ictal/${edfId}/bipolar-preview?${previewQs}`)
).json();
if (preview.pairs?.length) {
  cases.push({
    label: `tc=0.1 hc=70 ref=bipolar mains=50`,
    serverTc: 0.1,
    bandHigh: 70,
    mainsFreq: 50,
    reference: "none",
    channels: preview.pairs.slice(0, 20),
    rawReference: "bipolar",
  });
}

let worst = 0;
let failures = 0;
for (const c of cases) {
  const r = await run(c);
  worst = Math.max(worst, r.residual);
  const ok = r.residual <= TOLERANCE;
  if (!ok) failures++;
  console.log(`${ok ? "ok  " : "FAIL"}  ${r.residual.toExponential(2).padStart(9)}  ${r.label}`);
}

console.log(`\n${cases.length - failures}/${cases.length} within ${TOLERANCE}; worst residual ${worst.toExponential(2)}`);
process.exit(failures === 0 ? 0 : 1);
