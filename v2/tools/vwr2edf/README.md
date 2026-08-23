# vwr2edf - Micromed VWR to EDF+

Converts Micromed `.vwr` EEG recordings to one continuous EDF+C file, with
examiner notes exported as EDF+ annotations. It is a standalone pure-Python
tool

```bash
pip install numpy

python vwr2edf.py INPUT.vwr --list
python vwr2edf.py INPUT.vwr OUTDIR
python vwr2edf.py INPUT.vwr OUTDIR --patient "X X X X" --no-sidecar
```

The default output is `OUTDIR/INPUT.edf` plus `INPUT.json`. The sidecar retains
the LABCOD channel calibration and note frames that EDF cannot represent. It
does not read or emit the source patient's name, surname, or birth date. Keep
all converted recordings and sidecars out of git.

## Supported format

This includes a Python port of the relevant parts of Franco Milicchio's
`libvwr`: the 640-byte header, ORDER table, LABCOD table, 1,000 fixed note
slots, and continuous little-endian interleaved sample stream. Sample widths
of 1, 2, and 4 bytes are supported. The reader validates offsets, complete
frames, channel references, and calibration ranges before it writes output.

Each source sample is calibrated exactly as `libvwr`:

```text
(stored_value - lground) * (pmax - pmin) / (lmax - lmin + 1)
```

EDF stores 16-bit samples. `vwr2edf` scales each calibrated channel to its
source-width physical range; 32-bit VWR source integers therefore cannot be
bit-identical after EDF export. The calibrated physical values are retained to
EDF header precision.

Unparsed vendor segments (montages, video, triggers, and similar) are not
supported. VWR recordings are continuous, so the converter intentionally
writes one EDF rather than artificial clips.

## Verification

Run the synthetic test suite:

```bash
python test_vwr2edf.py
```

For a local VWR recording, use `--list` first, then compare non-identifying
structure and calibrated sample windows against the local `libvwr` `vwrdump`
reference. Do not commit recordings, generated CSV dumps, or metadata.

## License and attribution

`vwr2edf` is licensed under the BSD 3-Clause License. It includes portions
ported from `libvwr`, Copyright (C) Franco Milicchio, which are also subject to
the BSD 3-Clause License. See [LICENSE](LICENSE) for the full terms and
required notice.
