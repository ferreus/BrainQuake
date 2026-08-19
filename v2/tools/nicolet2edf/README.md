# nicolet2edf — Nicolet/Nervus `.e` → EDF+

Converts Nicolet/Nervus `.e` EEG files to EDF+C, one file per recorded segment.
Pure Python; no vendor software, no COM, no Windows requirement.

## Why this exists

There is no open-source reader for these files. The only thing that opens them
is `nrveeg_export.exe` (`NicoletEegImportOld`, Jan Brogger, University of
Bergen, 2007) — and using it means all of:

- It is a **2008 debug build whose C++ source was lost**. Its own README says
  so: *"This code was written in C++ in the early 2000s, but the source code was
  lost. Only the .exe file in the form of an installer is available."*
- It needs **proprietary Nicolet COM DLLs** (`XStorage`, `FileAccess4`,
  `DataStorage`, `AddinIf`) that only a Nicolet customer can obtain, registered
  in a 32-bit Windows registry.
- It exports to `.hcb`, a bespoke container that then needs a **second** script
  to reach EDF.

The widely circulated MATLAB reader `NicoletFile.m` does not handle this file
either. It reads the channel table as variable-length records with a `u32` size
prefix; these files use **fixed 552-byte records**, so on `Bella.e` that prefix
reads as `0x00700046` — the UTF-16 bytes of `Fp` — and the parse walks off the
end. See [Format](#format).

## Format

Everything below was verified against a real file. Byte offsets are absolute
unless stated.

### Container

| offset | type | meaning |
|---|---|---|
| `0x00` | 16 B | magic GUID `{1C7BC30F-2DCF-4B6D-8AEA-1F64CED2B917}` |
| `0x18` | u32 | offset of the main index |
| `0xAC` | u32 | tag count |
| `0xB0` | 84 B × n | tag table: `wchar[40]` name + `u32` section index |

The main index is a `u64` entry count followed by 24-byte entries
`{u64 sectionIdx, u64 offset, u32 blockL, u32 dataL}`.

A section's payload is its index entries concatenated in index order. **Read
`dataL`, not `blockL`** — they differ on a section's last block. Sections are
interleaved through the file in time order, so a section is never contiguous:
on `Bella.e` one channel's 44 blocks are spread from byte 2,269,400 to
92,512,648.

Tags named `"0"`, `"1"`, … are the per-channel sample streams: little-endian
`int16`, one section per channel, **never interleaved**.

The table is self-verifying: the spans it yields must end exactly on EOF.

### Channel table — `{A271CCCB-515D-4590-B6A1-DC170C8D6EE2}`

`u32` element count at `+752`, then **fixed 552-byte records from `+760`**:

| record offset | type | field |
|---|---|---|
| `+0x00` | wchar[32] | label |
| `+0x80` | wchar[32] | active sensor |
| `+0xC0` | wchar[32] | reference sensor |
| `+0x100` / `+0x108` | f64 | low cut / high cut |
| `+0x110` / `+0x118` | f64 | sampling rate / resolution, µV per LSB |
| `+0x120` / `+0x122` | u16 | mark / notch |

> Bytes `+0x40`…`+0x7F` are uninitialized heap — old pointer values, different
> on every file. They are not a field. Do not read them.

The reader rejects any file whose record size is not 552 bytes rather than
guessing, so a newer variant fails loudly instead of producing garbage.

### `SegmentStream` — 152 bytes per segment

`f64` OLE automation date (epoch 1899-12-30) at `+0x00`, `f64` duration in
seconds at `+0x10`.

### `Events` — variable-length packets

Walk by each packet's own length field.

| packet offset | type | field |
|---|---|---|
| `+0x00` / `+0x10` | 16 B / u64 | packet GUID / packet length |
| `+0x20` | f64 | timestamp, OLE date |
| `+0x30` | f64 | duration, seconds |
| `+0x88` | 16 B | event-type GUID |
| `+0xA8` | wchar\* | channel/derivation name, NUL-terminated |
| `+0x108` | wchar\* | label, NUL-terminated |

The base packet is 272 bytes and a longer label extends it. Both strings stop
at the **first** NUL: a packet can carry a stale tail from a longer string
written before it — on `Bella.e` one packet reads `onset\0` followed by `et\0`,
the end of a previous `offset`.

Recognised type GUIDs are in `nicolet.EVENT_TYPES` (Exam Start, Annotation,
Patient Event, Seizure, Hyperventilation, Impedance, Prune, Review Progress,
…). An unrecognised GUID is carried through verbatim rather than dropped.

## Segments

Segments are what the recorder actually stored, and **they are not contiguous**.
`Bella.e` holds 2750 s of data spanning 4 h 19 min of wall clock:

| # | start | duration |
|---|---|---|
| 0 | 2022-09-21 03:50:11 | 726 s |
| 1 | 2022-09-21 06:20:56 | 662 s |
| 2 | 2022-09-21 07:15:24 | 545 s |
| 3 | 2022-09-21 08:00:16 | 817 s |

The stored stream is these four back to back with the gaps removed, which is
also what the vendor `.hcb` export contains — with nothing recording that
2.5 h passed between the first two.

Default output is **one EDF per segment**, each with its true start time.
`--concat` writes the single glued file instead, and marks every join with an
annotation naming the next segment's wall-clock start and the size of the gap.

Anything spectral, connectivity-based, or fragility-based will read a join as a
real transient. Prefer per-segment files unless you specifically need the old
concatenated timeline.

## Channel selection

`Bella.e` carries 31 EEG channels at 512 Hz plus four **derived trend
channels** — `Rate`, `IBI`, `Bursts`, `Suppr` — at 1 Hz. Those are
burst-suppression numbers the Nicolet software computed, not recorded signal;
they have a blank reference sensor, which is how they are told apart.

Default output is the 31 EEG channels. `--all-channels` appends the trends at
their native 1 Hz — EDF stores samples-per-record per signal, so mixed rates
need no resampling.

## Scaling and polarity

Samples are written **as stored**: the data is native `int16`, so the EDF
digital range is the stored range and conversion is exactly lossless. Physical
range is `-32768 × resolution` to `32767 × resolution` (±5482 µV on `Bella.e`).

**The stored samples are inverted relative to µV, and this tool negates them by
default.** `--no-invert` writes them as stored. Negation happens at load, before
any montage subtracts, so bipolar arithmetic runs on corrected data.

This matches `nrveegimport.m` and the local `convert.py`, which both compute µV
as `-raw × res`. The widely circulated `NicoletFile.m` does **not** negate, and
is wrong here.

Two independent lines settle it.

**1. Against the viewer.** Segment 0 converted with the montage applied, opened
in EDFbrowser beside the same page in the Nicolet viewer (`Pedi 2(2)`, 30 mm/s,
70 µV/cm). Negated output matches trace for trace — the distinctive `C4-Cz` /
`Cz-C3` crossing pattern and the burst onset land in the same places.

**2. Against a recording whose polarity is already known** — this one needs no
viewer and no display convention at all. `datasets/BellaVEEG` is the same
patient's scalp VEEG, converted by `../nk2edf`, whose output is true µV. Measure
one signed statistic on the frontal-minus-occipital trace in both recordings;
only the *side* matters, since negating a signal flips the skew and turns an
x% positive peak rate into (100−x)%:

| | skew | large peaks positive |
|---|---|---|
| Nihon Kohden scalp, true µV, 92 clips / 193 min | **−0.173** (35/92 clips positive) | **41%** |
| — night-only subset, matching Bella.e's hours | −0.209 | 41% |
| Nicolet `raw × res`, 4 segments | **+0.395** (4/4 positive) | **59%** |

Opposite sign on both statistics, and 41% / 59% are exact complements. That is
a sign flip, so `raw × res` is inverted and negation is correct.

Two dead ends recorded so nobody repeats them:

- An earlier version of this argued from first principles that blinks are
  frontal-positive, so a majority-positive peak rate meant correct polarity.
  **That premise is false here** — ground-truth µV data from this patient reads
  41%, not >50%. Calibrating against a known recording removes the need to know
  why.
- It was then suspected that the premise failed because `Bella.e` starts at
  03:50 with the patient asleep. **Also wrong**: the night-only NKT subset gives
  the same −0.209 / 41%. Time of day is not the variable.

> EDFbrowser draws **positive upward** (a signal's own `-100 uV` gridline sits
> below its baseline). Since negated output matches the Nicolet viewer there,
> the Nicolet viewer must be positive-up too — worth knowing, as clinical EEG
> convention is normally negative-up. Line 2 above does not depend on this.

## Montages

The file stores the viewer's display montage in `{8A19AA48-BEA0-40D5-B89F-667FC578D635}`:

| offset | type | meaning |
|---|---|---|
| `+0x28` | wchar[32] | montage name |
| `+744` | u32 | derivation count |
| `+752` | 520 B × n | one record per trace |

Each record reuses the channel record's name fields — label at `+0x00`, active
sensor at `+0x80`, reference sensor at `+0xC0`. `752 + count × 520` equals the
section length exactly, which is what pins the stride down.

On `Bella.e` this is a 22-trace montage named `Pedi 2(2)`, and it reproduces
`datasets/Nicolet/montage.txt` — the same 22 labels in the same order,
including `EKG-Bipolar`, whose reference field is empty.

Output is **referential by default**, as stored, every channel against `REF`.
`--montage` writes the stored montage's bipolar traces instead: since every
channel shares `REF`, `A − B` cancels it exactly. A derivation naming no
reference (`EKG-Bipolar`) is written as its active channel alone, which is what
the viewer shows.

Bipolar output is halved so it stays in `int16` — a difference of two
full-scale channels needs 17 bits — and the physical range is doubled to match,
so the physical values are still right. Only the derivations that actually
subtract pay this; `EKG-Bipolar` keeps the full single-ended resolution.

The montage is recorded in the sidecar whether or not it is applied.

## Usage

```bash
pip install numpy
```

```bash
# list segments, channels and events; convert nothing
python nicolet2edf.py Bella.e --list

# one EDF per segment, 31 EEG channels                    -> OUTDIR/Bella_00_20220921035011.edf ...
python nicolet2edf.py Bella.e OUTDIR

# only some segments
python nicolet2edf.py Bella.e OUTDIR --segments 0,2-3

# include the 1 Hz derived trend channels
python nicolet2edf.py Bella.e OUTDIR --all-channels

# one glued file on the vendor timeline, joins annotated  -> OUTDIR/Bella.edf
python nicolet2edf.py Bella.e OUTDIR --concat

# the file's own bipolar display montage instead of referential channels
python nicolet2edf.py Bella.e OUTDIR --montage

# write samples as stored, without the default negation (see Scaling and polarity)
python nicolet2edf.py Bella.e OUTDIR --no-invert

# skip the per-EDF .json sidecar
python nicolet2edf.py Bella.e OUTDIR --no-sidecar
```

`nicolet.py` also runs standalone as a structure dumper:
`python nicolet.py Bella.e`.

### Sidecar

Each EDF gets a `.json` next to it holding what EDF cannot express: the full
segment table with wall-clock starts, every event with its type GUID, channel
and label (including the ones not written as annotations), and per-channel
resolution, active/reference sensor, filter and notch settings.

No patient name or identifier is read from the file into any output. The EDF
patient field defaults to `X X X X`.

## Verification

Run against `Bella.e` (98,212,760 B; 69 tags, 1596 index entries, 35 channels,
4 segments, 34 events).

**Self-checks, asserted in code.** These need nothing but the file, and each
one fails loudly on a misparse:

- Index block spans end at byte 98,212,760 — exactly EOF.
- Per channel, `sum(segment_duration × rate)` equals the stored sample count:
  `(726+662+545+817) × 512 = 1,408,000` and `× 1 = 2750`. The segment table and
  the data sections are written by different parts of the vendor software, so
  their agreeing is real evidence.
- The `Events` walk consumes its section exactly: 9336 bytes, 34 packets, no
  remainder.
- Every channel record is exactly 552 bytes and their count matches the
  declared element count.
- The montage section's `752 + count × 520` equals its length exactly.

**Round-trip, with independent readers.** Reading the output back with `edfio`
and `mne`:

| | |
|---|---|
| digital max \|err\| | **0** — across all 4 per-segment files and the 35-signal concatenated file |
| physical max \|err\| | 3.9e-3 µV, i.e. 2% of one LSB (0.167 µV) |
| digital max \|err\|, `--montage` | **0** on all 22 traces, against `(A−B)//2` computed from the source |
| physical max \|err\|, `--montage` | 7.8e-3 µV — twice the referential residual, as the doubled range implies |

**Montage against the viewer.** The decoded montage reproduces
`datasets/Nicolet/montage.txt` exactly: 22/22 labels, same order.

**Polarity.** Confirmed twice — against the Nicolet viewer, and against the
same patient's Nihon Kohden scalp VEEG whose polarity is already established.
See [Scaling and polarity](#scaling-and-polarity) for the numbers.

The physical residual is EDF's own limit, not a decode error: physical min/max
are 8 ASCII characters, and `-5482.46` is all that fits of `-5482.4645…`.

**Events against the viewer.** All 19 events in the viewer's own export
(`events.txt`) match decoded onsets within **1.00 s** once segment gaps are
collapsed, and the residual is always 0 or −1 s, never +1 — the viewer rounds
displayed elapsed time up. This validates the segment table, the OLE date
decoding and the event parser at once.

**Historical, not reproducible.** On 2026-08-20 all 35 channel streams decoded
from `Bella.e` were confirmed **byte-identical** to a `nrveeg_export.exe`
`.hcb` export of the same file (31 × 2,816,000 B + 4 × 5,500 B), and the
decoded channel table matched that export's header exactly on every field. The
vendor export was the oracle for this format; it has since been dropped, and
nothing in this tool reads or needs it.
