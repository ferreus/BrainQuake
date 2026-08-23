# nk2edf — Nihon Kohden → EDF+

Converts Nihon Kohden `.EEG` files to EDF+C, one file per saved review clip.
Handles both the **EEG-1200A** extended format (Neurofax JE-120A/225A) and the
legacy **EEG-1100** series (JE-921A and friends).

## Why this exists

The off-the-shelf converters reject the EEG-1200A files outright:

- **`nk2edf`** (van Beelen): `error, deviceblock has unknown signature: "EEG-1200A V01.00"`
- **MNE** `mne.io.read_raw_nihon`: `EEG-1200A V01.00` is not in its `_valid_headers`

Patching the whitelist is not enough. EEG-1200A uses a different datablock
layout from the EEG-1100 series, so the 1100-era field offsets misparse it —
on our reference file they yield `n_channels=1, duration=1.0 s` for a 12 GB
recording. The 1100 reader would silently produce garbage.

EEG-1100 files *are* handled by EDFbrowser's `nk2edf`, and that is exactly what
makes them valuable here: they give a reference implementation to check against.
Our EEG-1100C output is verified **sample-identical** to it (see Verification).

## Format

Both formats share the control chain. An EEG-1200A file opens with a legacy
1100-compatible **stub** datablock (this is what the old readers latch onto),
degraded to one channel; its end is where the extension begins:

| where | type | meaning |
|---|---|---|
| `0x91` / `0x92` | u8 / u32 | legacy control block count + address |
| `<ctl> +0x11` / `+0x12` | u8 / u32 | legacy datablock count + addresses, stride 20 |
| `<stub end> +0x01` | 16s | `"EEG-1200A V01.00"` |
| `<stub end> +0x11` / `+0x12` | u8 / u32 | extended control block count + addresses, stride 20 |
| `<ext ctl> +0x11` | u16 | extended datablock count |
| `<ext ctl> +0x14` | u64 × n | extended datablock addresses, stride 24 (recordings exceed 4 GB) |

On an EEG-1100 file there is no extension — the legacy datablocks *are* the
data. Every address must be read from the file: the first extended datablock
sits at `0x377B` in some recordings and `0x43FB` in others.

| offset | legacy | extended | meaning |
|---|---|---|---|
| `+0x00` | u8 | u8 | `0x01` |
| `+0x01` | 16s | 16s | `"TIMEhhmmss000000"` |
| `+0x14` | 6 B BCD | 20s ASCII | start `YY MM DD hh mm ss` / `"YYYYMMDDhhmmss000000"` |
| `+0x1A` | u16 | — | sampling rate, mask `0x3FFF` |
| `+0x1C` | u32 | — | duration, units of 0.1 s |
| `+0x26` | u8 | — | channel count |
| `+0x27` | 10 B × n | — | channel table; byte 0 = index into the `.21E` |
| `+0x28` | — | u32 | sampling rate (Hz) |
| `+0x2C` | — | u32 | duration, units of 0.1 s |
| `+0x44` | — | u32 | channel count |
| `+0x48` | — | 10 B × n | channel table |

Then `n_samples` frames of `(n_channels + 1)` little-endian u16.

> **Words `0 … n-1` are the channels, in table order. The _last_ word is the
> event/marker word.** Until 2026-08-19 this reader had it backwards — it
> treated word 0 as a mark word and shifted every channel by one, so a signal
> labelled `FZ` carried `CZ`'s data. Any EDF produced before that is wrong.
> EDFbrowser's `nk2edf.cpp` settles it: `channels = n_ch + 1` (:1201), the label
> table is read for `channels - 1` entries, and the final word is emitted as a
> signal literally named `Events/Markers` (:1241).

Channel words are offset-binary (subtract 32768). The event word is **not** —
EDFbrowser applies its high-byte fixup to the channel words only (:1349-1358),
so it is reinterpreted as int16 unchanged.

Blocks are stored back to back, but their addresses must still be read from the
table rather than assumed — the first one is not at a fixed offset. The table is
self-verifying: the spans it yields must tile the file without gaps or overlap
and end exactly on EOF.

Calibration: EEG inputs are ±3200 µV full scale (0.09765625 µV/LSB). `.21E`
indices 42–73 **and 76/77** are DC inputs at ±12002.9 mV — 76/77 are the mark
inputs and take the DC scale too (nk2edf.cpp:1262). `DCnn` maps to `.21E` index
`nn + 41`, so `DC01` is index 42; the `.11D` calibration is keyed off that
index, not off the channel's name, because a `.21E` may call index 42 `SpO2`.

## Sidecars

Given `NAME.EEG`, every `NAME.*` file **and folder** beside it is discovered and
used. All of it is optional — a missing sidecar degrades the output, never fails
the conversion.

| sidecar | what is taken from it |
|---|---|
| `.21E` | electrode labels, `[REFERENCE]` codes, `SystemReference`, device |
| `.PTN/` | montage definitions (below) |
| `.11D` | `[ConvertDisplay]`: per-DC-channel enable, coefficient, offset, unit |
| `.PNT` | sex and birth date **only** — never name or record number |
| `.sld` | clinician bookmarks (seizures, sleep stage, spike marks), µs precision |
| `.LOG` | technician log; also names which montage was live at any moment |
| `.EGF` | clip metadata and the video file list |

Ignored: `.CN2`/`.CN3` (duplicates the `.EEG` block table), `.BFT`/`.reg`/`.sd4`/
`.mg2*` (Persyst derivatives), `.EVT` (empty), `.TRD`, `.slf`, `.img`.

A `<name>.json` is written next to each EDF with the montages, reference,
per-channel units, demographics and log events — everything EDF has nowhere to
put. It is the schema shared with `nicolet2edf` and `vwr2edf`, documented in
[../SIDECAR.md](../SIDECAR.md). `--no-sidecar` skips it.

## Montages (`.PTN`)

Each `Pattern_NNN.PTN` is a fixed 59448-byte montage slot:

| offset | type | meaning |
|---|---|---|
| `0x0080` | 16s | montage name (`ETEST`, `EMU1`, …) |
| `0x0410 + i*0x50` | | channel record, 64 slots |
| `  +0x00` | u8 | electrode A — index into `.21E` `[ELECTRODE]` |
| `  +0x01` | u8 | electrode B — may be a `[REFERENCE]` code (`0x29` = `$0V`) |
| `  +0x02` | u8 | sensitivity (`0x0f` EEG, `0x05` EKG) |
| `  +0x07` | u8 | **visible** — 1 shown, 0 hidden |
| `  +0x0C` | u16 | display row, `0xFFFF` when hidden |

The visible flag is what sets the channel count. The byte at `0xD5` reads 19 in
every pattern and is **not** the count — trusting it silently truncates `ETEST`
from 23 channels to 19. The grid stops after 64 slots; past `0x1810` the bytes
are not records.

Verified against the Nihon Kohden viewer on `CA6476I6`: `ETEST` decodes to the
23-channel montage the viewer opens with, and `EMU1` to the 19-channel montage
it switches to at the `PAT EMU1 EEG` log entry — both exactly, `0V-EKG1`
included.

`--montage NAME` writes that montage's bipolar traces instead of referential
channels; `--montage auto` uses whichever pattern the `.LOG` names at each
block's start. An electrode the recording does not carry contributes zero, which
is how `0V-EKG1` comes out as `-EKG1`. A bipolar trace spans twice the
single-ended range, so montage output is halved into the int16 digital range
(±6400 µV full scale) rather than clipped.

## Channel selection

By default the exporter drops any channel whose `.21E` entry exists but is
**blank** — that is the recording stating no electrode is connected — and DC
inputs the `.11D` marks `Enable=FALSE`. On `CA6476I6` that is 30 channels of 73;
the old index-restart heuristic gave 71, of which 37 were blank placeholders,
and it dropped `DC23`/`DC24` outright.

Where the `.21E` has *no* entry at all, Nihon Kohden's built-in name is used,
ported from EDFbrowser's table (nk2edf.cpp:416-470) and verified to reproduce
all 228 of its entries. That is what turns indices 76/77 into `EEG Mark1` and
`EEG Mark2` rather than dropping them. A blank `.21E` entry does not fall back —
EDFbrowser only overrides its table when the value is non-empty.

The heuristic it replaces existed because the channel table can list more
channels than were connected — on the SEEG reference recording, entries 203–266
restart the `.21E` index sequence at 0 and carry only mains pickup (~350 µV RMS,
mutually correlated at r=0.999, 60 Hz power 1600–3400× background). Those are
unconnected headbox inputs; a label test excludes them without the index trick.

Pass `--all-channels` to keep everything and judge for yourself.

## Events

Three sources of event data exist, and earlier versions of this converter
discarded all of them. Every clip carried a valid but empty `EDF Annotations`
signal, so the clinical marks lived only in the `.LOG` and had to be transcribed
by hand against a per-clip time offset.

**The `.sld`** holds the clinician's bookmarks with microsecond timestamps —
the most clinically loaded data in the whole file set. On `CA6476I6` these are
seizure marks (`SZ 1P`, `SZ 2P epileptic spasms`), sleep staging and spike
localisations. They are merged into the annotation stream automatically.

**The `.LOG`** is read automatically if it sits next to the `.EEG`, and its
entries are written as EDF+ annotations. Events are matched to clips by
**absolute wall-clock time** — each datablock carries its own
`YYYYMMDDhhmmss` start, so an event either falls inside a clip or it does not.
This deliberately avoids the viewer's "elapsed" counter, which is cumulative
across the whole study rather than time within a clip.

**The event word** (the *last* word of every frame — patient button, technician
marks) is written as an `Events/Markers` signal, named and scaled exactly as
EDFbrowser does so output can be diffed against it. It is raw and
uninterpreted: preserving it beats guessing its encoding, and it can be decoded
later without re-converting. `--no-mark-channel` drops it.

> **The `.LOG` layout is not verified.** Unlike the datablock chain, which is
> self-checking (walking it must land exactly on EOF), the log has no such
> invariant and was reverse-engineered without a reference to test against.
> **Run `--dump-log` first** and compare the events against the Nihon Kohden
> viewer before trusting a converted file. If nothing lands inside a clip, the
> entry layout in `nk.read_log()` is wrong.

The EDF+ annotation signal is sized to fit the busiest record (at least 120
bytes, as before), so a dense log cannot overflow it.

## Usage

```bash
pip install numpy                      # required
pip install pyedflib                   # optional, for validation only

python nk2edf.py INPUT.EEG --dump-log              # check log parsing FIRST
python nk2edf.py INPUT.EEG --list                  # inventory, no writes
python nk2edf.py INPUT.EEG OUTDIR                  # all clips, with annotations
python nk2edf.py INPUT.EEG OUTDIR --blocks 0,3,17-20
python nk2edf.py INPUT.EEG OUTDIR --ascii-labels   # G'1 -> Gp1
python nk2edf.py INPUT.EEG OUTDIR --all-channels
python nk2edf.py INPUT.EEG OUTDIR --no-log         # ignore the .LOG
python nk2edf.py INPUT.EEG OUTDIR --annotations events.csv
python nk2edf.py INPUT.EEG OUTDIR --montage EMU1   # bipolar traces
python nk2edf.py INPUT.EEG OUTDIR --montage auto   # montage the .LOG names
python nk2edf.py INPUT.EEG OUTDIR --no-sidecar     # skip the .json
```

Requires the `.21E` file next to the `.EEG` for electrode labels; everything
else is optional. `--list` reports which sidecars were found, the montages
available, and the demographics, so you can see what information exists before
converting.

The EDF+ patient field carries **sex and birth date only** (`X F 23-NOV-2019 X`)
— age matters for interpretation, but the name and medical record number in the
`.PNT` are never written into a converted file. `--patient` overrides it.

`--annotations` takes a sidecar CSV, `<when>,<text>` per line with `#`
comments, where `when` is an ISO datetime (matched to clips like log entries)
or a bare number of seconds from the start of the clip (single-block
conversions only). Use it when the `.LOG` cannot be parsed, or to add marks
that were never in it:

```
2024-03-19T07:24:16,A LVFA -> broad
2024-03-19T07:24:28,EEG onset
```

Output is one EDF+C per clip, named `{stem}_{index}_{YYYYMMDDhhmmss}.edf`.
Each clip is continuous; clips are separated by gaps of minutes to hours, which
is why they are not concatenated.

## Verification

**Against EDFbrowser.** The strongest check available: `CA06911B.EEG`
(EEG-1100C) converted by this tool is **sample-identical** to EDFbrowser's
conversion of the same file across all 44 signals — every electrode, both
`EEG Mark` inputs, and the raw `Events/Markers` word. The only header
differences are deliberate: we take DC units from the `.11D` (`%`, `mmHg`)
where EDFbrowser writes `mV`, and we use 1 s data records where it uses 0.1 s.

**Against the montage ground truth.** `ETEST` and `EMU1` decode to exactly the
23- and 19-channel montages the Nihon Kohden viewer displays for `CA6476I6`.

**Alignment invariant.** On every EEG-1200A file the constant columns are
exactly the DC inputs the `.11D` marks disabled, plus unused mark inputs, and
the trailing event word is all-zero. This is the check that exposed the
off-by-one: under the old alignment `DA6465AU` had the real SEEG contact `K'12`
reading flat while `DC10` ran live.

**Byte accounting.** Block spans tile each file with no gaps or overlap and end
exactly on EOF — 93/42/39/63 blocks for the EEG-1200A files, 1 for `CA06911B`.

Conversion is lossless. Reading a converted file back with `pyedflib` (an
independent EDF implementation) and comparing against a direct re-read of the
`.EEG`:

```
digital  max|err| = 0            over 203 ch x 7000 samples
physical max|err| = 1.65e-12 mV  (per-channel calibration)
```

The annotation writer round-trips through MNE: events land at the requested
onsets, including several within one record, and the signal is sized so the
busiest record fits. That check does not cover `nk.read_log()`, which needs a
real `.LOG` — see the warning above.
