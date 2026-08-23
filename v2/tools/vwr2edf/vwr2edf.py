"""Convert Micromed VWR files to one continuous EDF+C recording."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import vwr

REC_SECS = 1
MIN_ANNOT_BYTES = 120
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _field(value: object, width: int) -> bytes:
    raw = str(value).encode("ascii", "replace")[:width]
    return raw + b" " * (width - len(raw))


def _number(value: float, width: int) -> bytes:
    for precision in range(8, -1, -1):
        text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
        if len(text) <= width:
            return _field(text, width)
    raise ValueError(f"{value} cannot fit in an EDF {width}-byte numeric field")


def _tal(onset: float, text: str | None = None) -> bytes:
    stamp = f"+{onset:.6f}".rstrip("0").rstrip(".")
    if text is None:
        return (stamp + "\x14\x14\x00").encode()
    clean = text.replace("\x00", " ").replace("\x14", " ").strip()
    return (stamp + "\x14" + clean + "\x14\x00").encode("utf-8")


def _annotations(header: vwr.Header, n_records: int) -> tuple[dict[int, list[bytes]], int]:
    by_record: dict[int, list[bytes]] = {}
    for note in header.notes:
        if note.frame > header.n_samples:
            continue
        onset = note.frame / header.frequency
        record = min(int(onset // REC_SECS), n_records - 1)
        by_record.setdefault(record, []).append(_tal(onset, note.description))
    widest = max(
        len(_tal(record * REC_SECS)) + sum(map(len, by_record.get(record, ())))
        for record in range(n_records)
    )
    return by_record, max(MIN_ANNOT_BYTES, widest + widest % 2)


def _unique_labels(channels: tuple[vwr.Channel, ...]) -> list[str]:
    labels, seen = [], {}
    for index, channel in enumerate(channels):
        label = channel.name or f"CH{index + 1}"
        label = label.encode("ascii", "replace").decode()[:16]
        count = seen.get(label, 0)
        seen[label] = count + 1
        if count:
            suffix = f"-{count + 1}"
            label = label[:16 - len(suffix)] + suffix
        labels.append(label)
    return labels


def _signal_ranges(header: vwr.Header) -> tuple[np.ndarray, np.ndarray]:
    raw_max = (1 << (header.int_size * 8)) - 1
    mins = np.asarray([-channel.lground * channel.factor for channel in header.channels], dtype=float)
    maxs = np.asarray(
        [(raw_max - channel.lground) * channel.factor for channel in header.channels], dtype=float
    )
    low, high = np.minimum(mins, maxs), np.maximum(mins, maxs)
    flat = low == high
    low[flat], high[flat] = -1.0, 1.0
    return low, high


def _header(
    source: vwr.Header,
    labels: list[str],
    physical_min: np.ndarray,
    physical_max: np.ndarray,
    n_records: int,
    patient: str,
    annotation_bytes: int,
) -> bytes:
    signals = len(labels) + 1
    start = source.start
    output = bytearray()
    output += _field("0", 8)
    output += _field(patient, 80)
    output += _field(f"Startdate {start.day:02d}-{MONTHS[start.month - 1]}-{start.year} X X Micromed", 80)
    output += _field(start.strftime("%d.%m.%y"), 8)
    output += _field(start.strftime("%H.%M.%S"), 8)
    output += _field(256 * (signals + 1), 8)
    output += _field("EDF+C", 44)
    output += _field(n_records, 8)
    output += _field(REC_SECS, 8)
    output += _field(signals, 4)
    output += b"".join(_field(label, 16) for label in labels) + _field("EDF Annotations", 16)
    output += b"".join(_field("", 80) for _ in range(signals))
    output += b"".join(_field("uV", 8) for _ in labels) + _field("", 8)
    output += b"".join(_number(value, 8) for value in physical_min) + _field("-1", 8)
    output += b"".join(_number(value, 8) for value in physical_max) + _field("1", 8)
    output += b"".join(_field(-32768, 8) for _ in range(signals))
    output += b"".join(_field(32767, 8) for _ in range(signals))
    output += b"".join(_field("", 80) for _ in range(signals))
    output += b"".join(_field(source.frequency, 8) for _ in labels)
    output += _field(annotation_bytes // 2, 8)
    output += b"".join(_field("", 32) for _ in range(signals))
    if len(output) != 256 * (signals + 1):
        raise AssertionError(f"EDF header is {len(output)} bytes")
    return bytes(output)


def _digital(
    frames: np.ndarray, header: vwr.Header, physical_min: np.ndarray, physical_max: np.ndarray
) -> np.ndarray:
    physical = vwr.calibrated(frames, header.channels)
    scale = 65535 / (physical_max - physical_min)
    digital = np.rint((physical - physical_min) * scale - 32768)
    return np.ascontiguousarray(np.clip(digital, -32768, 32767), dtype="<i2")


def convert(source: vwr.Header, output: Path, patient: str, sidecar: bool) -> None:
    n_records = max(1, -(-source.n_samples // source.frequency))
    labels = _unique_labels(source.channels)
    physical_min, physical_max = _signal_ranges(source)
    annotations, annotation_bytes = _annotations(source, n_records)

    with output.open("wb") as fh:
        fh.write(_header(source, labels, physical_min, physical_max, n_records, patient, annotation_bytes))
        records = vwr.iter_frames(source, source.frequency)
        for record in range(n_records):
            try:
                frames = next(records)
            except StopIteration:
                frames = np.empty((0, source.order), dtype=f"<u{source.int_size}")
            if len(frames) < source.frequency:
                frames = np.pad(frames, ((0, source.frequency - len(frames)), (0, 0)))
            digital = _digital(frames, source, physical_min, physical_max)
            fh.write(digital.T.tobytes())
            tal = _tal(record) + b"".join(annotations.get(record, ()))
            if len(tal) > annotation_bytes:
                raise AssertionError(f"annotations exceed record {record}'s allocated size")
            fh.write(tal + b"\0" * (annotation_bytes - len(tal)))

    if sidecar:
        metadata = {
            "source": source.path.name,
            "start": source.start.isoformat(),
            "duration_s": source.duration,
            "sampling_rate_hz": source.frequency,
            "source_sample_bytes": source.int_size,
            "channels": [
                {
                    "label": channel.label,
                    "ground": channel.ground,
                    "edf_label": label,
                    "labcod_index": channel.index,
                    "lmin": channel.lmin,
                    "lmax": channel.lmax,
                    "lground": channel.lground,
                    "pmin": channel.pmin,
                    "pmax": channel.pmax,
                    "factor": channel.factor,
                    "units": channel.units,
                }
                for channel, label in zip(source.channels, labels, strict=True)
            ],
            "notes": [{"frame": note.frame, "onset_s": note.frame / source.frequency,
                       "description": note.description} for note in source.notes],
        }
        with output.with_suffix(".json").open("w", encoding="utf-8") as fh:
            json.dump(metadata, fh, indent=2)


def _list(header: vwr.Header) -> None:
    print(f"{header.path}  {header.size / 1e6:.1f} MB")
    print(f"{header.order} channels, {header.frequency} Hz, {header.duration:.3f} s, "
          f"{len(header.notes)} notes")
    for index, channel in enumerate(header.channels):
        print(f"{index:3d} {channel.name:<16} L={channel.lmin}:{channel.lmax} "
              f"G={channel.lground} factor={channel.factor:g}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("outdir", nargs="?")
    parser.add_argument("--list", action="store_true", help="inspect the VWR file without writing")
    parser.add_argument("--patient", default="X X X X", help="anonymous EDF patient field")
    parser.add_argument("--no-sidecar", action="store_true", help="skip the JSON metadata sidecar")
    args = parser.parse_args()

    header = vwr.read_header(args.input)
    if args.list:
        _list(header)
        return
    if not args.outdir:
        raise SystemExit("outdir required (or use --list)")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / f"{Path(args.input).stem}.edf"
    convert(header, output, args.patient, not args.no_sidecar)
    print(f"{output.name}  {header.order} ch  {header.duration:.3f} s  {len(header.notes)} annotations")


if __name__ == "__main__":
    main()
"""Micromed VWR to EDF+ converter."""

# Portions are derived from libvwr, Copyright (C) Franco Milicchio.
# SPDX-License-Identifier: BSD-3-Clause
