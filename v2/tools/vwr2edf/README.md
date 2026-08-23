# vwr2edf - Micromed VWR to EDF+

Converts Micromed `.vwr` (Brain-Quick) EEG recordings to one continuous EDF+C
file, with every marker area exported as EDF+ annotations and the stored
montages available as bipolar output. Standalone pure Python.

```bash
pip install numpy

python vwr2edf.py INPUT.vwr --list
python vwr2edf.py INPUT.vwr OUTDIR
python vwr2edf.py INPUT.vwr OUTDIR --montage auto
python vwr2edf.py INPUT.vwr OUTDIR --montage "Longit Bipolar T1 T2"
python vwr2edf.py INPUT.vwr OUTDIR --patient "X X X X" --no-sidecar
```

The default output is `OUTDIR/INPUT.edf` plus `INPUT.json` -- the shared
converter sidecar, see [../SIDECAR.md](../SIDECAR.md). It does not read or emit
the source patient's name, surname, or birth date. Keep all converted
recordings and sidecars out of git.

## Supported format

The 640-byte header, ORDER table, LABCOD table, 1,000 note slots and the
continuous little-endian interleaved sample stream are a port of Franco
Milicchio's `libvwr`. Sample widths of 1, 2 and 4 bytes are supported. The
reader validates offsets, complete frames, channel references and calibration
ranges before it writes output.

Segments are looked up by name in the 29-slot directory at byte 176, so their
order in the file does not matter.

Each source sample is calibrated exactly as `libvwr`:

```text
(stored_value - lground) * (pmax - pmin) / (lmax - lmin + 1)
```

EDF stores 16-bit samples. `vwr2edf` scales each calibrated signal to its
physical range; 32-bit VWR source integers therefore cannot be bit-identical
after EDF export. A montage trace spans two channels, so its physical range is
the difference of theirs and one LSB is twice as coarse as the referential
channel's.

### Montages

`libvwr` skips the montage areas ("montages are present but I don't care right
now"). The `MONTAGE` area is 30 slots of 4096 bytes:

| offset | type | field |
|---|---|---|
| 0 | `u16` | `lines`, the number of traces |
| 2 | `u16` | `sectors` |
| 4 | `u16` | `base_time`, seconds per page |
| 6 | `u16` | `notch` |
| 8 | `u16[64]` | colour |
| 136 | `u16[64]` | selection |
| 264 | `char[64]` | montage name |
| 328 | `u16[128]` | per trace `i`: `[2i]` reference, `[2i+1]` active, both LABCOD indices |
| 584.. | `u32[64]` x3 | filter and reference arrays, layout unverified -- not exported |

`MAX_CAN_VIEW` is 64, not the 128 some Micromed notes assume; that is what puts
the name at +264 and makes the struct 4096 bytes. A slot with `lines == 0` is
empty.

Input index 0 is the recording ground. The viewer renders such a trace against
the channel's own LABCOD ground -- `ECG1+` shows as `ECG1+-ECG1-`, not
`ECG1+-G2` -- and so does this converter. Since the ground is not a recorded
channel, that trace stays single-ended and is exactly lossless.

`HISTORY` is `u32[128]` montage-change sample times followed by 30 more montage
slots; its first slot is the montage the recording was made with, which is what
`--montage auto` selects.

### Marker areas

| area | record | becomes |
|---|---|---|
| `NOTE` | 1000 x `{u32 frame; char[40] text}` | point annotation |
| `TRIGGER` | `{u32 sample; u16 type}`, `0xFFFFFFFF` = empty slot | point annotation `Trigger N` |
| `FLAGS` | `{u32 begin; u32 end}` | interval annotation `Flag N` |
| `EVENT A` / `EVENT B` | `{char[64] name; u32 begin[100]; u32 end[100]}` | interval annotation named by the area |
| `TRONCA` | `{u32 original_sample; u32 sample}` | `segments[]` in the sidecar, not an annotation -- it repeats the part notes |

Intervals are written with an EDF+ `\x15` duration. `DVIDEO` (video sync) and
`BRAINIMG` are parsed by nobody.

VWR recordings are continuous, so the converter writes one EDF rather than
artificial clips.

## Verification

```bash
python test_vwr2edf.py          # synthetic reader/montage/marker/EDF tests
python ../test_sidecar_schema.py  # sidecar schema shared with nk2edf/nicolet2edf
```

Against a real 36-channel 256 Hz recording:

- all three stored montages decode, and the 21 traces of `Longit Bipolar T1 T2`
  match the vendor viewer's list in order, including `ECG1+-ECG1-`;
- `HISTORY` names that same montage as the one in use;
- every montage trace read back through MNE lands within half an LSB of
  `calibrated(active) - calibrated(reference)`, and the single-ended ECG trace
  is exact;
- the `TRIGGER`, `EVENT A`, `EVENT B` and `FLAGS` areas of that file are empty
  (all 8192 trigger slots are `0xFFFFFFFF`), so those parsers are covered by
  the synthetic tests only and are **not yet verified against a real file that
  carries them**.

For a local recording, use `--list` first, then compare non-identifying
structure and calibrated sample windows against the local `libvwr` `vwrdump`
reference. Do not commit recordings, generated CSV dumps, or metadata.

## License and attribution

`vwr2edf` is licensed under the BSD 3-Clause License. It includes portions
ported from `libvwr`, Copyright (C) Franco Milicchio, which are also subject to
the BSD 3-Clause License. See [LICENSE](LICENSE) for the full terms and
required notice.
