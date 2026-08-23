"""Reader for the Micromed VWR/Brain-Quick format.

The header, ORDER, LABCOD and NOTE areas are a Python port of libvwr. The
MONTAGE, HISTORY, TRIGGER, TRONCA, FLAGS and EVENT areas were reverse
engineered from a recording -- libvwr skips all of them. See README.md for the
struct layouts.
"""

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

# MAX_CAN_VIEW is 64 here, not the 128 some Micromed notes assume; that is what
# puts the name at +264 and makes the struct 4096 bytes.
MONTAGE_SIZE = 4096
MONTAGE_TRACES = 64
MONTAGE_NAME = 264
MONTAGE_NAME_SIZE = 64
MONTAGE_INPUTS = 328
HISTORY_TIMES = 512  # u32[128] montage-change samples, then montage slots

TRIGGER_SIZE = 6  # u32 sample, u16 type
TRONCA_SIZE = 8  # u32 original sample, u32 sample
FLAGS_SIZE = 8  # u32 begin, u32 end
EVENT_NAME_SIZE = 64
EVENT_COUNT = 100
NO_SAMPLE = 0xFFFFFFFF


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
    used: bool
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
class MontageTrace:
    label: str
    active: str
    reference: str
    active_index: int
    reference_index: int


@dataclass(frozen=True)
class Montage:
    name: str
    notch: int
    base_time: int
    traces: tuple[MontageTrace, ...]


@dataclass(frozen=True)
class Marker:
    frame: int
    label: str
    kind: str
    source: str
    end_frame: int | None = None


@dataclass(frozen=True)
class Part:
    frame: int
    original_frame: int


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
    labcod: tuple[Channel, ...]
    notes: tuple[Note, ...]
    markers: tuple[Marker, ...]
    parts: tuple[Part, ...]
    montages: tuple[Montage, ...]
    recorded_montage: str | None
    n_samples: int

    @property
    def duration(self) -> float:
        return self.n_samples / self.frequency

    def montage(self, name: str) -> Montage | None:
        return next((m for m in self.montages if m.name == name), None)


def _cstring(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1", "replace").strip()


def _read_exact(fh: BinaryIO, size: int, what: str) -> bytes:
    data = fh.read(size)
    if len(data) != size:
        raise EOFError(f"truncated {what}")
    return data


def _read_segment(fh: BinaryIO, segment: Segment | None) -> bytes:
    if segment is None:
        return b""
    fh.seek(segment.offset)
    return _read_exact(fh, segment.size, f"{segment.name} segment")


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


def _channel_table(raw: bytes) -> tuple[Channel, ...]:
    if len(raw) < LABCOD_SIZE or len(raw) % LABCOD_SIZE:
        raise ValueError(f"LABCOD segment has invalid size {len(raw)}")
    out = []
    for index in range(len(raw) // LABCOD_SIZE):
        at = index * LABCOD_SIZE
        used = raw[at]
        label = _cstring(raw[at + 2:at + 8])
        ground = _cstring(raw[at + 8:at + 14])
        lmin, lmax, lground, pmin, pmax = struct.unpack_from("<iiiii", raw, at + 14)
        if lmax - lmin + 1 == 0:
            raise ValueError(f"LABCOD channel {index} has a zero calibration range")
        factor = (pmax - pmin) / (lmax - lmin + 1)
        units = struct.unpack_from("<h", raw, at + 34)[0]
        out.append(Channel(index, label, ground, bool(used), lmin, lmax, lground,
                           pmin, pmax, factor, units))
    return tuple(out)


def _notes(raw: bytes) -> tuple[Note, ...]:
    want = NOTE_COUNT * NOTE_SIZE
    if len(raw) < want:
        raise ValueError(f"NOTE segment has {len(raw)} bytes, expected at least {want}")
    out = []
    for at in range(0, want, NOTE_SIZE):
        frame = struct.unpack_from("<I", raw, at)[0]
        text = _cstring(raw[at + 4:at + NOTE_SIZE])
        if frame != NO_SAMPLE and text:
            out.append(Note(frame, text))
    return tuple(out)


def _montages(raw: bytes, labcod: tuple[Channel, ...]) -> tuple[Montage, ...]:
    def label_of(index: int) -> str:
        return labcod[index].label if index < len(labcod) else f"#{index}"

    out = []
    for at in range(0, len(raw) - MONTAGE_SIZE + 1, MONTAGE_SIZE):
        lines, _sectors, base_time, notch = struct.unpack_from("<4H", raw, at)
        if not 1 <= lines <= MONTAGE_TRACES:
            continue
        name = _cstring(raw[at + MONTAGE_NAME:at + MONTAGE_NAME + MONTAGE_NAME_SIZE])
        inputs = struct.unpack_from(f"<{MONTAGE_TRACES * 2}H", raw, at + MONTAGE_INPUTS)
        traces = []
        for trace in range(lines):
            reference_index, active_index = inputs[trace * 2], inputs[trace * 2 + 1]
            active = label_of(active_index)
            # Input 0 is the recording ground: the viewer shows such a trace
            # against the channel's own ground, e.g. ECG1+ against ECG1-.
            if reference_index:
                reference = label_of(reference_index)
            elif active_index < len(labcod):
                reference = labcod[active_index].ground
            else:
                reference = ""
            traces.append(MontageTrace(
                f"{active}-{reference}" if reference else active,
                active, reference, active_index, reference_index,
            ))
        out.append(Montage(name, notch, base_time, tuple(traces)))
    return tuple(out)


def _triggers(raw: bytes) -> list[Marker]:
    out = []
    for at in range(0, len(raw) - TRIGGER_SIZE + 1, TRIGGER_SIZE):
        frame, kind = struct.unpack_from("<IH", raw, at)
        if frame != NO_SAMPLE:
            out.append(Marker(frame, f"Trigger {kind}", "trigger", "TRIGGER"))
    return out


def _flags(raw: bytes) -> list[Marker]:
    out = []
    for index in range(len(raw) // FLAGS_SIZE):
        begin, end = struct.unpack_from("<II", raw, index * FLAGS_SIZE)
        if begin != end and begin != NO_SAMPLE:
            out.append(Marker(begin, f"Flag {index + 1}", "flag", "FLAGS", end))
    return out


def _events(raw: bytes, source: str) -> list[Marker]:
    if len(raw) < EVENT_NAME_SIZE + EVENT_COUNT * 8:
        return []
    name = _cstring(raw[:EVENT_NAME_SIZE]) or source
    begins = struct.unpack_from(f"<{EVENT_COUNT}I", raw, EVENT_NAME_SIZE)
    ends = struct.unpack_from(f"<{EVENT_COUNT}I", raw, EVENT_NAME_SIZE + EVENT_COUNT * 4)
    return [
        Marker(begin, name, "event", source, end)
        for begin, end in zip(begins, ends)
        if begin != end and begin != NO_SAMPLE
    ]


def _parts(raw: bytes) -> tuple[Part, ...]:
    out = []
    for at in range(0, len(raw) - TRONCA_SIZE + 1, TRONCA_SIZE):
        original, frame = struct.unpack_from("<II", raw, at)
        if original or frame:
            out.append(Part(frame, original))
    return tuple(out)


def read_header(path: str | os.PathLike[str]) -> Header:
    """Read metadata and validate the data stream without loading samples."""
    source = Path(path)
    size = source.stat().st_size
    if size < HEADER_SIZE:
        raise ValueError(f"{source} is smaller than the {HEADER_SIZE}-byte VWR header")

    with source.open("rb") as fh:
        raw = _read_exact(fh, HEADER_SIZE, "VWR header")
        segments = _segments(raw, size)
        by_name = {segment.name: segment for segment in segments}
        missing = [name for name in ("ORDER", "LABCOD", "NOTE") if name not in by_name]
        if missing:
            raise ValueError(f"VWR header lacks the {', '.join(missing)} segment(s)")

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

        if by_name["ORDER"].size < ORDER_SIZE:
            raise ValueError(f"ORDER segment has {by_name['ORDER'].size} bytes, "
                             f"expected at least {ORDER_SIZE}")
        fh.seek(by_name["ORDER"].offset)
        order_table = struct.unpack("<256H", _read_exact(fh, ORDER_SIZE, "ORDER segment"))
        labcod = _channel_table(_read_segment(fh, by_name["LABCOD"]))
        if max(order_table[:order]) >= len(labcod):
            raise ValueError("ORDER references a missing LABCOD channel")
        notes = _notes(_read_segment(fh, by_name["NOTE"]))
        montages = _montages(_read_segment(fh, by_name.get("MONTAGE")), labcod)
        history = _montages(_read_segment(fh, by_name.get("HISTORY"))[HISTORY_TIMES:], labcod)
        parts = _parts(_read_segment(fh, by_name.get("TRONCA")))
        markers = [Marker(note.frame, note.description, "note", "NOTE") for note in notes]
        markers += _triggers(_read_segment(fh, by_name.get("TRIGGER")))
        markers += _flags(_read_segment(fh, by_name.get("FLAGS")))
        markers += _events(_read_segment(fh, by_name.get("EVENT A")), "EVENT A")
        markers += _events(_read_segment(fh, by_name.get("EVENT B")), "EVENT B")

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
        labcod=labcod,
        notes=notes,
        markers=tuple(sorted(markers, key=lambda m: (m.frame, m.label))),
        parts=parts,
        montages=montages,
        recorded_montage=history[0].name if history else None,
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
