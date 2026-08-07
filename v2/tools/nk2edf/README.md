# nk2edf — Nihon Kohden EEG-1200A → EDF+

Converts Nihon Kohden `.EEG` files written by **EEG-1200A** systems (Neurofax
JE-120A/225A headbox) to EDF+C, one file per saved review clip.

## Why this exists

The off-the-shelf converters reject these files outright:

- **`nk2edf`** (van Beelen): `error, deviceblock has unknown signature: "EEG-1200A V01.00"`
- **MNE** `mne.io.read_raw_nihon`: `EEG-1200A V01.00` is not in its `_valid_headers`

Patching the whitelist is not enough. EEG-1200A uses a different datablock
layout from the EEG-1100 series, so the 1100-era field offsets misparse it —
on our reference file they yield `n_channels=1, duration=1.0 s` for a 12 GB
recording. The 1100 reader would silently produce garbage.

## Format

The file opens with a legacy 1100-compatible **stub** datablock at `0x17FE`
(this is what the old readers latch onto). The real data is a chain of
extended datablocks starting at `0x43FB`, each laid out as:

| offset | type | meaning |
|---|---|---|
| `+0x00` | u8 | `0x01` |
| `+0x01` | 16s | `"TIMEhhmmss000000"` |
| `+0x14` | 20s | `"YYYYMMDDhhmmss000000"` |
| `+0x28` | u32 | sampling rate (Hz) |
| `+0x2C` | u32 | duration, units of 0.1 s |
| `+0x44` | u32 | channel count |
| `+0x48` | 10 B × n | channel table; byte 0 = index into the `.21E` |

Then `n_samples` frames of `(n_channels + 1)` little-endian u16. Word 0 of each
frame is the mark/event word; the rest are channel samples, offset-binary
(subtract 32768).

Blocks are stored back to back, so the next block starts at
`data_address + n_samples * (n_channels + 1) * 2`. The chain is
self-verifying: walking it must land exactly on EOF.

Calibration (JE-120A/225A): EEG inputs are ±3200 µV full scale
(0.09765625 µV/LSB); `.21E` indices 42–73 are DC inputs at ±12002.9 mV.

## Channel selection

The channel table can list more channels than were actually connected. On the
reference recording, entries 203–266 restart the `.21E` index sequence at 0 and
carry only mains pickup — ~350 µV RMS, mutually correlated at r=0.999, with
60 Hz power 1600–3400× background. Those are unconnected headbox inputs.

By default the exporter stops where the `.21E` index sequence restarts. Pass
`--all-channels` to keep everything and judge for yourself.

## Usage

```bash
pip install numpy                      # required
pip install pyedflib                   # optional, for validation only

python nk2edf.py INPUT.EEG --list                  # inventory, no writes
python nk2edf.py INPUT.EEG OUTDIR                  # all clips
python nk2edf.py INPUT.EEG OUTDIR --blocks 0,3,17-20
python nk2edf.py INPUT.EEG OUTDIR --ascii-labels   # G'1 -> Gp1
python nk2edf.py INPUT.EEG OUTDIR --all-channels
```

Requires the `.21E` file next to the `.EEG` for electrode labels.

Output is one EDF+C per clip, named `{stem}_{index}_{YYYYMMDDhhmmss}.edf`.
Each clip is continuous; clips are separated by gaps of minutes to hours, which
is why they are not concatenated.

## Verification

Conversion is lossless. Reading a converted file back with `pyedflib` (an
independent EDF implementation) and comparing against a direct re-read of the
`.EEG`:

```
digital  max|err| = 0            over 203 ch x 7000 samples
physical max|err| = 1.65e-12 mV  (per-channel calibration)
```
