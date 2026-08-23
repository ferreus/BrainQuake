# vwr2edf: montage extractor, full event extraction, unified sidecar schema

*Implemented 2026-08-23. Kept as the format reverse-engineering record; the
struct layouts below are the source of truth for `v2/tools/vwr2edf/vwr.py`.*

## Context

`v2/tools/vwr2edf` converts Micromed Brain-Quick `.vwr` recordings to EDF+C. It lags its two
sibling converters (`v2/tools/nk2edf`, `v2/tools/nicolet2edf`) in three ways:

1. **No montage extraction.** `vwr.py` never touches the `MONTAGE` area; `README.md:39` explicitly
   lists montages as unsupported. The reference C library it was ported from punted too
   (`libvwr.h:41-42` — *"montages are present but I don't care right now"*). Both sibling tools
   extract montages into their sidecar *and* can apply them to the EDF signals via `--montage`.
2. **Events come only from `NOTE`.** The `TRIGGER`, `EVENT A`, `EVENT B` and `FLAGS` areas are
   never read (`libvwr.h:62-63` — *"trigger seems to be unused by the ocx"*).
3. **Three divergent JSON sidecar schemas.** `vwr2edf`, `nk2edf` and `nicolet2edf` each invent
   their own; nothing is shared beyond the key name `channels` (with disjoint sub-keys) and
   `montage_applied` (with different *types* — a name string in nk2edf, a bool in nicolet2edf).
   A consumer cannot read all three with one code path.

Outcome wanted: `vwr2edf` reports and can apply the montages stored in the file, extracts every
marker area the format defines, and all three converters emit the same sidecar schema.

---

## Format findings (reverse-engineered from `~/learn/BellaCure/vwr/Bella.vwr`)

`Bella.vwr` is a Micromed Brain-Quick file, header type `4`, 36 channels @ 256 Hz,
6,869,569 frames (7 h 27 m), data at byte 648170. Its 16 header descriptors tile bytes
0–648170 contiguously; every area has been dumped.

### MONTAGE area — descriptor 7, offset 129696, 122880 bytes

30 slots of **4096 bytes**. Struct (confirmed against all three stored montages):

| offset | type | field |
|---|---|---|
| 0 | `u16` | `lines` — number of traces |
| 2 | `u16` | `sectors` |
| 4 | `u16` | `base_time` (s/page) |
| 6 | `u16` | `notch` |
| 8 | `u16[64]` | `colour` |
| 136 | `u16[64]` | `selection` |
| **264** | `char[64]` | **`description`** — montage name |
| **328** | `u16[128]` | **`inputs`** — per trace `i`: `inputs[2i]` = **reference**, `inputs[2i+1]` = **active**, both LABCOD indices |
| 584 | `u32[64]` | high-pass filter (values unverified — do not export) |
| 840 | `u32[64]` | low-pass filter (unverified) |
| 1096 | `u32[64]` | reference flags (unverified) |
| 1352.. | | free |

`MAX_CAN_VIEW` is **64**, not the 128 some Micromed docs assume — that is what puts
`description` at +264 and makes the struct 4096 bytes.

Slots in `Bella.vwr`: `Longit Bipolar T1 T2` (21 traces), `Ref A1 A2` (22), `Ref CZ T1 T2` (21).
Slots 3–29 are empty (`lines == 0`, blank description).

Decoding montage 0 reproduces the user's list exactly, in order:
`Fp2-F4, F4-C4, C4-P4, P4-O2, Fp1-F3, F3-C3, C3-P3, P3-O1, Fp2-F8, F8-T4, T4-T6, T6-O2,
Fp1-F7, F7-T3, T3-T5, T5-O1, Fz-Cz, Cz-Pz, T2-Pz, T1-Pz, ECG1+-ECG1-`.

**Reference index 0** is LABCOD `G2`, the recording ground. The viewer renders such a trace
against the channel's own LABCOD `ground` field — which is why trace 20 shows as `ECG1+ ECG-`
(LABCOD 167 = label `ECG1+`, ground `ECG1-`) rather than `ECG1+ G2`. Resolve reference index 0
to the active channel's `ground`; keep the raw indices in the sidecar so nothing is lost.

### HISTORY area — descriptor 10, offset 252714, 123392 bytes

`u32[128]` montage-change sample times (all `0xFFFFFFFF` here = no changes), then 30 more
4096-byte montage slots. Only slot 0 is populated, holding **`Longit Bipolar T1 T2`** — i.e.
the montage in use at acquisition. This is what `--montage auto` should resolve to.

### Marker areas

| area | offset | size | record | content in `Bella.vwr` |
|---|---|---|---|---|
| `NOTE` | 83072 | 44000 | 1000 × `{u32 frame; char[40] text}` | **6 entries** |
| `TRONCA` | 127872 | 800 | 100 × `{u32 orig_sample; u32 sample}` | 6 entries, `sample` column equals the 6 note frames — the part boundaries |
| `FLAGS` | 127072 | 800 | 100 × `{u32 begin; u32 end}` | all zero |
| `EVENT A` | 392490 | 864 | `{char[64] name; u32 begin[100]; u32 end[100]}` | name blank, all zero |
| `EVENT B` | 393354 | 864 | same | name blank, all zero |
| `TRIGGER` | 394218 | 49152 | 8192 × `{u32 sample; u16 type}` | **every slot is `FFFFFFFF/0000`** — empty |
| `DVIDEO` | 376106 | 16384 | 16-byte `{u32 start_ms; u32 duration_ms; u32 index; u32 -1}` | 4 video segments (`start_ms == frame/256*1000`) |

The 6 notes are stored literally as `* * * Part N * * *` (spaced, not `***`), at frames
1, 376274, 1432341, 1525352, 5164417, **5500698**. Note there is a **Part 6** the user's list
does not mention.

### The missing "Trigger 1" — deferred

**Not a blocker for this work.** `Trigger 1` is not recoverable from `Bella.vwr`; the user will
confirm later whether this is the file the trigger was seen in. Evidence gathered so far:
- `TRIGGER`, `EVENT A`, `EVENT B`, `FLAGS` are all empty (byte histogram of the 49152-byte
  trigger block is exactly 32768 × `0xFF` + 16384 × `0x00`).
- The string `Trigger` appears nowhere in the 495 MB file, in ASCII or UTF-16LE.
- The `MKR+` channel (ORDER position 35, LABCOD 174) was scanned across all 6.87 M frames: it
  is a continuous two-level square wave (32256/33280), not marker pulses.
- The 16 header descriptors tile the whole metadata region with no unaccounted bytes.

Additionally the user's wall-clock times do not fit any linear sample→time mapping: between
Part 4 (frame 1525352) and Part 5 (frame 5164417) the file holds 3,639,065 frames = 14215 s at
256 Hz, but the quoted times are only 3990 s apart. Those timestamps therefore come from a
different source than this `.vwr`.

**Plan decision:** implement the `TRIGGER` / `EVENT A` / `EVENT B` / `FLAGS` parsers anyway — they
are ~40 lines, the unified schema needs a place for them, and they are correct for files that do
carry markers. Do **not** chase the trigger further in `Bella.vwr`. Verification against a real
file carrying triggers is deferred until the user identifies the right source file; the parsers
are covered by synthetic tests in the meantime and will need no code change to verify later.

---

## Work

### 1. New `v2/tools/edfcommon.py` — shared EDF/TAL helpers + one sidecar builder

Today `_fld`/`_num`/`_tal`/`plan_annotations`/`parse_sel` exist in **three** verbatim copies
(`nk2edf.py:108-180,344-358`, `nicolet2edf.py:51-124,351-365`, `vwr2edf.py:17-51` renamed to
`_field`/`_number`/`_annotations`), with `nicolet2edf.py:46-49` carrying a "fix a bug in one and
fix it in the other" comment. Since the goal is a schema all three share, hoist them:

- Move `_fld`, `_num`, `_tal`, `plan_annotations`, `parse_sel`, `REC_SECS`, `MIN_ANNOT_BYTES`,
  `MONTHS` into `edfcommon.py`.
- Add `build_sidecar(**parts) -> dict` producing the schema below.
- Each tool gains a two-line bootstrap before `import edfcommon`:
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`. The tools stay runnable
  from their own directory, as documented in their READMEs.
- Delete the three local copies and the "fix one, fix the other" comment.

`build_header` and `signal_spec` stay per-tool — they legitimately differ (nk2edf has one global
sfreq, nicolet2edf carries per-signal samples-per-record, vwr2edf rescales physical→digital).

### 2. Unified sidecar schema (`eeg2edf-sidecar/1`)

Every key is always present; a format with no such concept emits `null` or `[]`.

```json
{
  "schema": "eeg2edf-sidecar/1",
  "source": { "file": "Bella.vwr", "format": "micromed-vwr" },
  "clip":   { "index": null, "start": "2025-06-29T12:17:22", "offset_s": 0.0,
              "duration_s": 26834.254, "sfreq_hz": 256.0 },
  "device": null,
  "reference": null,
  "patient": { "sex": null, "dob": null, "age_at_recording": null },
  "channels": [ { "label": "Fp1-G2", "edf_label": "Fp1-G2", "source_index": 1,
                  "unit": "uV", "sfreq_hz": 256.0, "reference": "G2",
                  "resolution_uv_per_lsb": 0.19073, "low_cut": null, "high_cut": null,
                  "notch": null, "derived": false } ],
  "montages": [ { "name": "Longit Bipolar T1 T2",
                  "channels": [ { "label": "Fp2-F4", "active": "Fp2", "reference": "F4",
                                  "active_index": 2, "reference_index": 4 } ] } ],
  "montage_applied": "Longit Bipolar T1 T2",
  "segments": [ { "index": 0, "start": "...", "offset_s": 0.0, "duration_s": 1469.8 } ],
  "events":   [ { "onset_s": 0.004, "duration_s": null, "label": "* * * Part 1 * * *",
                  "type": "note", "source": "NOTE", "channel": null } ]
}
```

Rules that resolve the current divergence:
- `clip.start` is **always ISO 8601** (nk2edf currently emits the raw `"YYYYMMDDhhmmss"` string).
- `montages` is **always a list** (nicolet2edf currently emits a single dict or `null` — wrap its
  one montage in a list).
- `montage_applied` is **always a name string or `null`** (nicolet2edf currently emits a bool —
  emit the montage's own name instead).
- `events` is **always present** (nk2edf currently has none; populate it from the same `.LOG` /
  `.sld` / `--annotations` events it already writes into the EDF, with `source` set to
  `"LOG"` / `"sld"` / `"csv"`).
- `channels[]` describes **the signals actually written to the EDF**. Both sibling tools currently
  describe the referential selection even under `--montage`; fix by building `channels[]` from the
  same label list the EDF header uses.
- Per-file constants (`montages`, `segments`, `events`) stay whole-file lists repeated in each
  per-clip sidecar, as today — `clip` identifies which slice the file covers.

### 3. `v2/tools/vwr2edf/vwr.py` — parse MONTAGE, HISTORY and the marker areas

- Look segments up **by name** instead of `segments[:3]` positionally (`vwr.py:166`); the
  parsed `Segment.name` is currently discarded. Required now that we need `MONTAGE`, `TRIGGER`,
  etc., which are not at fixed list positions in every file.
- Add constants next to the existing block at `vwr.py:18-26`: `MONTAGE_SIZE = 4096`,
  `MONTAGE_LINES/NOTCH/DESC/INPUTS` offsets, `MAX_TRACES = 64`, `TRIGGER_SIZE = 6`,
  `TRONCA_SIZE = 8`, `FLAGS_SIZE = 8`, `EVENT_SIZE = 864`.
- New dataclasses: `MontageTrace(label, active, reference, active_index, reference_index)`,
  `Montage(name, notch, base_time, traces)`, `Marker(frame, end_frame, label, kind, source)`.
- `_montages(raw)` — decode a 4096-byte slot; skip slots with `lines == 0` or `lines > 64`.
  Resolve labels through the LABCOD table; reference index 0 → the active channel's `ground`.
- `_markers(...)` — one function per area returning `Marker`s:
  `NOTE` → `kind="note"`; `TRIGGER` → `label=f"Trigger {type}"`, skip `sample == 0xFFFFFFFF`;
  `FLAGS`/`EVENT A`/`EVENT B` → interval markers with `end_frame`, skip `begin == end == 0`;
  `TRONCA` → `kind="part"`, sidecar-only (it duplicates the `Part N` notes).
  Sort the merged list by frame.
- Extend `Header` with `montages`, `recorded_montage` (HISTORY slot 0's name, or `None`),
  `markers`, `parts`. Keep `notes` as-is so the existing tests keep passing.
- Fix the two latent bugs in the note reader (`vwr.py:128-137`): a note at frame 0 is dropped by
  the `if struct.unpack_from(...)[0] and ...` guard, and the frame is unpacked twice per slot.
- Delete the identical `if used: / else:` branches at `vwr.py:121-124` and put `used` on
  `Channel` instead.

### 4. `v2/tools/vwr2edf/vwr2edf.py` — `--montage`, all markers, new sidecar

- `--montage NAME|auto` (value flag, matching `nk2edf.py:427-431`; `auto` → `recorded_montage`).
  Fail loudly on an unknown name, listing what the file has.
- `montage_columns(montage, header)` maps LABCOD indices to ORDER positions, mirroring
  `nk2edf.py:92-105`. A trace whose *active* channel is not recorded is dropped; a trace whose
  *reference* is not recorded (index 0 / `G2`) stays single-ended — that is what the viewer shows
  for `ECG1+`.
- Arithmetic is simpler here than in the siblings: `vwr2edf` already rescales physical→digital
  (`_digital`, `vwr2edf.py:117-123`), so compute `phys[a] - phys[b]` in float and set the trace's
  physical range to `(min_a - max_b, max_a - min_b)`. No `//2` headroom hack, no doubled range.
- `_annotations` now walks `header.markers` instead of `header.notes`, **sorted by onset**
  (currently unsorted, `vwr2edf.py:41`), emits `\x15{duration}` for interval markers, and uses
  `note.frame >= header.n_samples` (currently `>`, admitting an onset one sample past the end).
- Fix the short-record padding bug (`vwr2edf.py:140-141`): the tail is zero-padded on **raw**
  values then calibrated, so padding becomes `(0 - lground) * factor` — a large DC step. Pad with
  each channel's `lground` so the padding is physical zero.
- `--list` also prints the montages and the marker count.
- Replace the sidecar dict (`vwr2edf.py:149-176`) with `edfcommon.build_sidecar(...)`. Keep the
  VWR-specific calibration fields (`lmin`/`lmax`/`lground`/`pmin`/`pmax`/`factor`) inside each
  `channels[]` entry — they are the reproducibility record for the scaling.
- Move the stray docstring + libvwr attribution from after `if __name__ == "__main__"`
  (`vwr2edf.py:212-215`) into the module header where `help()` can see it.

### 5. `nk2edf` / `nicolet2edf` — schema conformance only

No behaviour changes beyond the sidecar. Rewrite `write_sidecar` in each
(`nk2edf.py:361-396`, `nicolet2edf.py:308-348`) to call `edfcommon.build_sidecar`, mapping:

| unified | nk2edf | nicolet2edf |
|---|---|---|
| `source.format` | `"nihon-kohden"` | `"nicolet-nervus"` |
| `clip.index/start/duration_s/sfreq_hz` | block index, `blk["start"]` **parsed to ISO**, `blk["duration"]`, `blk["sfreq"]` | `clip[0]`, `clip[1]`, `clip[3]`, `null` (per-channel) |
| `montages` | existing list, plus `active_index`/`reference_index` from the `a`/`b` bytes already parsed in `nkmeta.read_ptn_dir` (currently discarded) | its single montage wrapped in a list |
| `montage_applied` | unchanged (name or `null`) | **name string** instead of bool |
| `events` | **new** — from `events_for_block` + `.sld` + `--annotations` | existing list, `when`→`onset_s` via `stream_seconds` |
| `segments` | `[]` | existing |
| `patient` | existing | nulls |
| `polarity_inverted` | — | keep as a top-level extra key |

### 6. Docs and tests

- `docs/plans/vwr2edf-montage-events.md` — this plan, committed to the repo (per repo convention
  that implementation plans live in `docs/plans/`).
- `v2/tools/SIDECAR.md` — the schema above, one table of per-format population.
- `vwr2edf/README.md` — replace the "montages … not supported" line (`:39`) with the decoded
  MONTAGE/HISTORY/marker-area layout tables from this plan, and a **Verification** section
  recording that `Bella.vwr`'s `TRIGGER`/`EVENT A`/`EVENT B`/`FLAGS` areas are empty.
- `nk2edf/README.md`, `nicolet2edf/README.md` — point their sidecar sections at `SIDECAR.md`.
- `vwr2edf/test_vwr2edf.py` — extend the synthetic writer (`write_vwr`, `:22-69`) to emit a
  MONTAGE area with two montages (one with a `reference == 0` trace), a HISTORY area naming the
  second, a `TRIGGER` entry, an `EVENT A` interval, and a note at frame 0. Add tests for:
  montage decode + label resolution; `--montage` output channel count/order and physical range;
  marker merge order; frame-0 note survival; sidecar schema keys present. These are the only
  automated tests in the tool family — worth keeping them the strongest part.

---

## Verification

```bash
cd /home/ferreus/dev/BrainQuake/v2/tools/vwr2edf
python -m pytest test_vwr2edf.py -q          # synthetic round-trip, incl. new montage/marker tests

# Real file: inspect first
python vwr2edf.py ~/learn/BellaCure/vwr/Bella.vwr --list
#   expect: 36 ch, 256 Hz, 26834.254 s, 6 markers
#   montages: Longit Bipolar T1 T2 (21), Ref A1 A2 (22), Ref CZ T1 T2 (21); recorded = the first

# Referential conversion (unchanged signals, new sidecar)
python vwr2edf.py ~/learn/BellaCure/vwr/Bella.vwr /tmp/vwrout
python -c "import json;d=json.load(open('/tmp/vwrout/Bella.json'));
print(d['schema'], len(d['channels']), [m['name'] for m in d['montages']], len(d['events']))"

# Montage conversion — the 21 traces must match the user's list in order
python vwr2edf.py ~/learn/BellaCure/vwr/Bella.vwr /tmp/vwrmtg --montage auto
python -c "import mne;r=mne.io.read_raw_edf('/tmp/vwrmtg/Bella.edf');print(r.ch_names);print(r.annotations)"
```

Cross-checks that make the montage decode trustworthy:
- The 21 decoded labels equal the user's list, in order (already confirmed offline against the
  raw bytes; the test asserts it against the file).
- `Fp2-F4` from the montage must equal `Fp1-G2`-style referential `Fp2` minus `F4` sample-for-sample
  within one LSB — assert on the first 256 frames.
- All three converters' sidecars validate against the same key set:
  `python -c "import json,sys; ..."` over one output from each tool.

Schema parity for the siblings needs a Nihon Kohden and a Nicolet file to re-run
(`nk2edf/README.md:235-268`, `nicolet2edf/README.md:265-308` name the ones used before).

## Deferred

`Trigger 1` will not appear in the output — `Bella.vwr`'s `TRIGGER`/`EVENT A`/`EVENT B`/`FLAGS`
areas are empty (see Format findings). The parsers ship and are exercised by synthetic tests, but
stay unverified against a real Micromed file that carries triggers. The user will confirm later
which file the trigger was actually seen in; nothing here needs to change to verify it then.
