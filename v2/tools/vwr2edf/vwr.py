"""Reader for the Micromed VWR subset implemented by libvwr."""

# Portions are derived from libvwr, Copyright (C) Franco Milicchio.
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

import datetime as dt
import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

HEADER_SIZE = 640
SEGMENT_COUNT = 29
SEGMENT_FIRST = 176
SEGMENT_SIZE = 16
ORDER_SIZE = 256 * 2
LABCOD_SIZE = 128
NOTE_COUNT = 1000
NOTE_SIZE = 44


@dataclass(frozen=True)
class Segment:
    name: str
    offset: int
    size: int


@dataclass(frozen=True)
class Channel:
    index: int
    label: str
    ground: str
    lmin: int
    lmax: int
    lground: int
    pmin: int
    pmax: int
    factor: float
    units: int

    @property
    def name(self) -> str:
        return f"{self.label}-{self.ground}" if self.ground else self.label


@dataclass(frozen=True)
class Note:
    frame: int
    description: str


@dataclass(frozen=True)
class Header:
    path: Path
    size: int
    magic: str
    start: dt.datetime
    data_offset: int
    order: int
    frequency: int
    int_size: int
    checksum: int
    file_type: int
    segments: tuple[Segment, ...]
    channels: tuple[Channel, ...]
    notes: tuple[Note, ...]
    n_samples: int

    @property
    def duration(self) -> float:
        return self.n_samples / self.frequency


def _cstring(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def _read_exact(fh: BinaryIO, size: int, what: str) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise EOFError(f"truncated {what}")
    return data


def _segments(raw: bytes, size: int) -> tuple[Segment, ...]:
    out = []
    for slot in range(SEGMENT_COUNT):
        at = SEGMENT_FIRST + slot * SEGMENT_SIZE
        name = _cstring(raw[at:at + 8])
        if not name:
            break
        offset, length = struct.unpack_from("<II", raw, at + 8)
        if offset + length > size:
            raise ValueError(f"segment {name!r} exceeds file size")
        out.append(Segment(name, offset, length))
    return tuple(out)


def _channel_table(fh: BinaryIO, segment: Segment) -> tuple[Channel, ...]:
    if segment.size < LABCOD_SIZE or segment.size % LABCOD_SIZE:
        raise ValueError(f"LABCOD segment has invalid size {segment.size}")
    raw = _read_exact(fh, segment.size, "LABCOD segment")
    out = []
    for index in range(segment.size // LABCOD_SIZE):
        at = index * LABCOD_SIZE
        used, _unknown = struct.unpack_from("<BB", raw, at)
        label = _cstring(raw[at + 2:at + 8])
        ground = _cstring(raw[at + 8:at + 14])
        lmin, lmax, lground, pmin, pmax = struct.unpack_from("<iiiii", raw, at + 14)
        if lmax - lmin + 1 == 0:
            raise ValueError(f"LABCOD channel {index} has a zero calibration range")
        factor = (pmax - pmin) / (lmax - lmin + 1)
        units = struct.unpack_from("<h", raw, at + 34)[0]
        if used:
            out.append(Channel(index, label, ground, lmin, lmax, lground, pmin, pmax, factor, units))
        else:
            out.append(Channel(index, label, ground, lmin, lmax, lground, pmin, pmax, factor, units))
    return tuple(out)


def _notes(fh: BinaryIO, segment: Segment) -> tuple[Note, ...]:
    want = NOTE_COUNT * NOTE_SIZE
    if segment.size < want:
        raise ValueError(f"NOTE segment has {segment.size} bytes, expected at least {want}")
    raw = _read_exact(fh, want, "NOTE segment")
    return tuple(
        Note(struct.unpack_from("<I", raw, at)[0], _cstring(raw[at + 4:at + NOTE_SIZE]))
        for at in range(0, want, NOTE_SIZE)
        if struct.unpack_from("<I", raw, at)[0] and _cstring(raw[at + 4:at + NOTE_SIZE])
    )


def read_header(path: str | os.PathLike[str]) -> Header:
    """Read metadata and validate the data stream without loading samples."""
    source = Path(path)
    size = source.stat().st_size
    if size < HEADER_SIZE:
        raise ValueError(f"{source} is smaller than the {HEADER_SIZE}-byte VWR header")

    with source.open("rb") as fh:
        raw = _read_exact(fh, HEADER_SIZE, "VWR header")
        segments = _segments(raw, size)
        if len(segments) < 3:
            raise ValueError("VWR header lacks ORDER, LABCOD, or NOTE segments")

        data_offset = struct.unpack_from("<I", raw, 138)[0]
        order = struct.unpack_from("<H", raw, 142)[0]
        frequency = struct.unpack_from("<H", raw, 146)[0]
        int_size = struct.unpack_from("<H", raw, 148)[0]
        if not 1 <= order <= 256:
            raise ValueError(f"invalid channel count {order}")
        if frequency == 0:
            raise ValueError("sampling frequency is zero")
        if int_size not in (1, 2, 4):
            raise ValueError(f"unsupported VWR sample width {int_size}")
        if not HEADER_SIZE <= data_offset <= size:
            raise ValueError(f"invalid data offset {data_offset}")

        order_segment, labcod_segment, note_segment = segments[:3]
        if order_segment.size < ORDER_SIZE:
            raise ValueError(f"ORDER segment has {order_segment.size} bytes, expected at least {ORDER_SIZE}")
        fh.seek(order_segment.offset)
        order_table = struct.unpack("<256H", _read_exact(fh, ORDER_SIZE, "ORDER segment"))
        fh.seek(labcod_segment.offset)
        labcod = _channel_table(fh, labcod_segment)
        if max(order_table[:order]) >= len(labcod):
            raise ValueError("ORDER references a missing LABCOD channel")
        fh.seek(note_segment.offset)
        notes = _notes(fh, note_segment)

    data_bytes = size - data_offset
    frame_bytes = order * int_size
    if data_bytes % frame_bytes:
        raise ValueError(f"data stream has {data_bytes % frame_bytes} trailing byte(s)")
    n_samples = data_bytes // frame_bytes
    channels = tuple(labcod[index] for index in order_table[:order])

    date_parts = raw[128:134]
    try:
        start = dt.datetime(  # noqa: DTZ001
            1900 + date_parts[2], date_parts[1], date_parts[0], *date_parts[3:]
        )
    except ValueError as exc:
        raise ValueError("invalid VWR exam date/time") from exc
    return Header(
        path=source,
        size=size,
        magic=_cstring(raw[:32]),
        start=start,
        data_offset=data_offset,
        order=order,
        frequency=frequency,
        int_size=int_size,
        checksum=struct.unpack_from("<H", raw, 160)[0] & 0x7FFF,
        file_type=raw[175],
        segments=segments,
        channels=channels,
        notes=notes,
        n_samples=n_samples,
    )


def iter_frames(header: Header, frames: int) -> Iterator[np.ndarray]:
    """Yield raw unsigned sample frames in source channel order."""
    if frames < 1:
        raise ValueError("frames must be positive")
    dtype = np.dtype(f"<u{header.int_size}")
    with header.path.open("rb") as fh:
        fh.seek(header.data_offset)
        remaining = header.n_samples
        while remaining:
            take = min(frames, remaining)
            raw = _read_exact(fh, take * header.order * header.int_size, "sample data")
            yield np.frombuffer(raw, dtype=dtype).reshape(take, header.order)
            remaining -= take


def calibrated(frames: np.ndarray, channels: tuple[Channel, ...]) -> np.ndarray:
    """Apply libvwr's per-channel physical calibration."""
    ground = np.asarray([c.lground for c in channels], dtype=np.float64)
    factor = np.asarray([c.factor for c in channels], dtype=np.float64)
    return (frames.astype(np.float64) - ground) * factor
