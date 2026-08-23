"""Convert Micromed VWR files to one continuous EDF+C recording.

Notes, triggers, flags and the EVENT A/B areas are written as EDF+
annotations. --montage writes one of the file's stored montages as bipolar
traces instead of the referential channels.

Usage:
  python vwr2edf.py INPUT.vwr OUTDIR [--montage NAME|auto] [--list]
                                     [--patient "X X X X"] [--no-sidecar]
"""

# Portions are derived from libvwr, Copyright (C) Franco Milicchio.
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import vwr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import edfcommon  # noqa: E402

REC_SECS = edfcommon.REC_SECS
EQUIPMENT = "Micromed"


def _unique_labels(names: list[str]) -> list[str]:
    """EDF readers key on labels, so they have to be distinct and fit 16 bytes."""
    labels, seen = [], {}
    for index, name in enumerate(names):
        label = (name or f"CH{index + 1}").encode("ascii", "replace").decode()[:16]
        count = seen.get(label, 0)
        seen[label] = count + 1
        if count:
            suffix = f"-{count + 1}"
            label = label[:16 - len(suffix)] + suffix
        labels.append(label)
    return labels


def montage_pairs(header: vwr.Header, montage: vwr.Montage) -> list[tuple[str, int, int | None]]:
    """Map a montage's LABCOD indices onto ORDER positions.

    A trace whose reference is not recorded -- the ground, input 0 -- stays
    single-ended, which is what the viewer shows for ECG1+.
    """
    pos: dict[int, int] = {}
    for position, channel in enumerate(header.channels):
        pos.setdefault(channel.index, position)
    out = []
    for trace in montage.traces:
        active = pos.get(trace.active_index)
        if active is None:
            continue
        out.append((trace.label, active, pos.get(trace.reference_index)))
    return out


def referential_pairs(header: vwr.Header) -> list[tuple[str, int, int | None]]:
    return [(channel.name, position, None) for position, channel in enumerate(header.channels)]


def _channel_ranges(header: vwr.Header) -> tuple[np.ndarray, np.ndarray]:
    """Physical span of each source channel over the full stored integer range."""
    raw_max = (1 << (header.int_size * 8)) - 1
    mins = np.asarray([-c.lground * c.factor for c in header.channels], dtype=float)
    maxs = np.asarray([(raw_max - c.lground) * c.factor for c in header.channels], dtype=float)
    return np.minimum(mins, maxs), np.maximum(mins, maxs)


def _signal_ranges(
    header: vwr.Header, pairs: list[tuple[str, int, int | None]]
) -> tuple[np.ndarray, np.ndarray]:
    low, high = _channel_ranges(header)
    mins, maxs = [], []
    for _label, a, b in pairs:
        mins.append(low[a] - (high[b] if b is not None else 0.0))
        maxs.append(high[a] - (low[b] if b is not None else 0.0))
    mins, maxs = np.asarray(mins), np.asarray(maxs)
    flat = mins == maxs
    mins[flat], maxs[flat] = -1.0, 1.0
    return mins, maxs


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
    fld = edfcommon._fld
    output = bytearray()
    output += fld("0", 8)
    output += fld(patient, 80)
    output += fld(edfcommon.recording_field(start, source.path.stem, EQUIPMENT), 80)
    output += fld(start.strftime("%d.%m.%y"), 8)
    output += fld(start.strftime("%H.%M.%S"), 8)
    output += fld(256 * (signals + 1), 8)
    output += fld("EDF+C", 44)
    output += fld(n_records, 8)
    output += fld(REC_SECS, 8)
    output += fld(signals, 4)
    output += b"".join(fld(label, 16) for label in labels) + fld("EDF Annotations", 16)
    output += b"".join(fld("", 80) for _ in range(signals))
    output += b"".join(fld("uV", 8) for _ in labels) + fld("", 8)
    output += b"".join(edfcommon._num(value, 8) for value in physical_min) + fld("-1", 8)
    output += b"".join(edfcommon._num(value, 8) for value in physical_max) + fld("1", 8)
    output += b"".join(fld(-32768, 8) for _ in range(signals))
    output += b"".join(fld(32767, 8) for _ in range(signals))
    output += b"".join(fld("", 80) for _ in range(signals))
    output += b"".join(fld(source.frequency, 8) for _ in labels)
    output += fld(annotation_bytes // 2, 8)
    output += b"".join(fld("", 32) for _ in range(signals))
    if len(output) != 256 * (signals + 1):
        raise AssertionError(f"EDF header is {len(output)} bytes")
    return bytes(output)


def _digital(
    frames: np.ndarray,
    header: vwr.Header,
    pairs: list[tuple[str, int, int | None]],
    physical_min: np.ndarray,
    physical_max: np.ndarray,
) -> np.ndarray:
    physical = vwr.calibrated(frames, header.channels)
    signal = np.stack(
        [physical[:, a] - (physical[:, b] if b is not None else 0.0) for _l, a, b in pairs],
        axis=1,
    )
    scale = 65535 / (physical_max - physical_min)
    digital = np.rint((signal - physical_min) * scale - 32768)
    return np.ascontiguousarray(np.clip(digital, -32768, 32767), dtype="<i2")


def _events(header: vwr.Header) -> list[tuple[float, str, float | None]]:
    """Every marker area as (onset, text, duration), in onset order."""
    out = []
    for marker in header.markers:
        if marker.frame >= header.n_samples:
            continue
        end = min(marker.end_frame, header.n_samples) if marker.end_frame else None
        duration = (end - marker.frame) / header.frequency if end else None
        out.append((marker.frame / header.frequency, marker.label, duration))
    return sorted(out)


def _sidecar(
    source: vwr.Header,
    pairs: list[tuple[str, int, int | None]],
    labels: list[str],
    physical_min: np.ndarray,
    physical_max: np.ndarray,
    montage: vwr.Montage | None,
) -> dict:
    def channel(index: int) -> dict:
        _label, a, b = pairs[index]
        active, reference = source.channels[a], (source.channels[b] if b is not None else None)
        span = (physical_max[index] - physical_min[index]) / 65535
        return {
            "label": pairs[index][0],
            "edf_label": labels[index],
            "source_index": active.index,
            "unit": "uV",
            "sfreq_hz": source.frequency,
            "reference": reference.label if reference else (active.ground or None),
            "resolution_uv_per_lsb": span,
            "derived": b is not None,
            "lmin": active.lmin,
            "lmax": active.lmax,
            "lground": active.lground,
            "pmin": active.pmin,
            "pmax": active.pmax,
            "factor": active.factor,
            "units": active.units,
        }

    ends = [part.frame for part in source.parts[1:]] + [source.n_samples]
    return edfcommon.build_sidecar(
        source_file=source.path.name,
        source_format="micromed-vwr",
        clip={
            "start": source.start.isoformat(),
            "duration_s": source.duration,
            "sfreq_hz": source.frequency,
        },
        patient=None,
        channels=[channel(index) for index in range(len(pairs))],
        montages=[
            {"name": m.name, "channels": [vars(t) for t in m.traces]}
            for m in source.montages
        ],
        montage_applied=montage.name if montage else None,
        segments=[
            {
                "index": index,
                "offset_s": part.frame / source.frequency,
                "duration_s": (end - part.frame) / source.frequency,
                "original_frame": part.original_frame,
            }
            for index, (part, end) in enumerate(zip(source.parts, ends))
        ],
        events=[
            {
                "onset_s": marker.frame / source.frequency,
                "duration_s": ((marker.end_frame - marker.frame) / source.frequency
                               if marker.end_frame else None),
                "label": marker.label,
                "type": marker.kind,
                "source": marker.source,
                "frame": marker.frame,
            }
            for marker in source.markers
        ],
        source_sample_bytes=source.int_size,
        recorded_montage=source.recorded_montage,
    )


def convert(
    source: vwr.Header,
    output: Path,
    patient: str,
    sidecar: bool,
    montage: vwr.Montage | None = None,
) -> int:
    n_records = max(1, -(-source.n_samples // source.frequency))
    pairs = montage_pairs(source, montage) if montage else referential_pairs(source)
    if not pairs:
        raise SystemExit(f"montage {montage.name!r} names no recorded channel")
    labels = _unique_labels([label for label, _a, _b in pairs])
    physical_min, physical_max = _signal_ranges(source, pairs)
    annotations, annotation_bytes = edfcommon.plan_annotations(_events(source), n_records)
    # Pad the last short record at each channel's ground, i.e. physical zero.
    ground = np.asarray([c.lground for c in source.channels])
    pad_row = np.clip(ground, 0, (1 << (source.int_size * 8)) - 1).astype(f"<u{source.int_size}")

    with output.open("wb") as fh:
        fh.write(_header(source, labels, physical_min, physical_max, n_records,
                         patient, annotation_bytes))
        records = vwr.iter_frames(source, source.frequency)
        for record in range(n_records):
            try:
                frames = next(records)
            except StopIteration:
                frames = np.empty((0, source.order), dtype=f"<u{source.int_size}")
            if len(frames) < source.frequency:
                pad = np.broadcast_to(pad_row, (source.frequency - len(frames), source.order))
                frames = np.concatenate([frames, pad])
            digital = _digital(frames, source, pairs, physical_min, physical_max)
            fh.write(digital.T.tobytes())
            fh.write(edfcommon.record_annotations(record, annotations, annotation_bytes))

    if sidecar:
        edfcommon.write_sidecar(
            output, _sidecar(source, pairs, labels, physical_min, physical_max, montage)
        )
    return len(pairs)


def _list(header: vwr.Header) -> None:
    print(f"{header.path}  {header.size / 1e6:.1f} MB")
    print(f"{header.order} channels, {header.frequency} Hz, {header.duration:.3f} s, "
          f"{len(header.markers)} markers, {len(header.parts)} parts")
    for montage in header.montages:
        mark = "*" if montage.name == header.recorded_montage else " "
        print(f"{mark}montage {montage.name!r}, {len(montage.traces)} traces: "
              + ", ".join(trace.label for trace in montage.traces))
    for index, channel in enumerate(header.channels):
        print(f"{index:3d} {channel.name:<16} L={channel.lmin}:{channel.lmax} "
              f"G={channel.lground} factor={channel.factor:g}")
    for marker in header.markers:
        print(f"  {marker.frame / header.frequency:10.3f}s  [{marker.source}] {marker.label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("outdir", nargs="?")
    parser.add_argument("--list", action="store_true", help="inspect the VWR file without writing")
    parser.add_argument("--patient", default="X X X X", help="anonymous EDF patient field")
    parser.add_argument("--montage", help="write a stored montage's traces instead of the "
                                          "referential channels; NAME or 'auto'")
    parser.add_argument("--no-sidecar", action="store_true", help="skip the JSON metadata sidecar")
    args = parser.parse_args()

    header = vwr.read_header(args.input)
    if args.list:
        _list(header)
        return
    if not args.outdir:
        raise SystemExit("outdir required (or use --list)")

    montage = None
    if args.montage:
        want = header.recorded_montage if args.montage.lower() == "auto" else args.montage
        if want is None:
            raise SystemExit("no montage recorded in HISTORY; pass --montage NAME instead")
        montage = header.montage(want)
        if montage is None:
            raise SystemExit(f"no montage {want!r} -- available: "
                             + ", ".join(repr(m.name) for m in header.montages))

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / f"{Path(args.input).stem}.edf"
    n_signals = convert(header, output, args.patient, not args.no_sidecar, montage)
    print(f"{output.name}  {n_signals} ch  {header.duration:.3f} s  "
          f"{len(header.markers)} annotations"
          + (f"  montage {montage.name}" if montage else ""))


if __name__ == "__main__":
    main()
