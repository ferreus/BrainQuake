"""Nicolet/Nervus .e reader.

Section offsets come from the file's own tag table and index -- the only fixed
offsets are the six header fields below.

  0x00   16 B          magic GUID {1C7BC30F-2DCF-4B6D-8AEA-1F64CED2B917}
  0x18   u32           offset of the main index
  0xAC   u32           tag count
  0xB0   84 B x n      tag table: wchar[40] name + u32 section index

main index:
  +0x00  u64           entry count
  +0x08  24 B x n      u64 section, u64 offset, u32 blockL, u32 dataL

A section's payload is its entries concatenated in index order; read dataL, not
blockL -- they differ on a section's last block. Sections are interleaved, so
one is never contiguous. Tags named "0".."n" are the per-channel sample
streams: little-endian int16, one section per channel, never interleaved.

TSGUID {A271CCCB-...} carries the channel table: u32 count at +752, then FIXED
552-byte records from +760:
  +0x00  wchar[32]     label
  +0x80  wchar[32]     active sensor
  +0xC0  wchar[32]     reference sensor
  +0x100 f64 / f64     low cut / high cut
  +0x110 f64 / f64     sampling rate / resolution (uV per LSB)
  +0x120 u16 / u16     mark / notch

The fixed record is what makes these files "old". NicoletFile.m reads a
variable-length record with a u32 size prefix at +0; here that prefix is the
UTF-16 bytes of the label ("Fp" -> 0x00700046), so it walks off the end.
Bytes +0x40..0x7F are uninitialized heap and mean nothing.

DERIVATIONGUID {8A19AA48-...} carries the viewer's display montage: its name at
+0x28, u32 derivation count at +744, then 520-byte records from +752 reusing
the channel record's label/active/reference fields. 752 + count*520 is the
section length exactly, which is the check that the stride is right.

SegmentStream is 152 B per segment: f64 OLE start date at +0x00, f64 duration
seconds at +0x10. The channel stream is the segments back to back with the
wall-clock gaps between them removed.

Events are variable-length packets, walked by their own length field:
  +0x00  16 B          packet GUID
  +0x10  u64           packet length
  +0x20  f64           timestamp, OLE date
  +0x30  f64           duration, seconds
  +0x88  16 B          event type GUID
  +0xA8  wchar*        channel/derivation name, NUL-terminated
  +0x108 wchar*        label, NUL-terminated; base packet is 272 B and a longer
                       label extends it
Both strings stop at the first NUL -- a packet can carry a stale tail from a
longer string written before it.
"""
import datetime as dt
import struct

MAGIC = bytes.fromhex("0fc37b1ccf2d6d4b8aea1f64ced2b917")
INDEX_PTR = 0x18
TAG_COUNT = 0xAC
TAG_FIRST = 0xB0
TAG_STRIDE = 84
TAG_NAME = 80  # wchar[40], then the u32 section index
IDX_STRIDE = 24

TS_GUID = "{A271CCCB-515D-4590-B6A1-DC170C8D6EE2}"
TS_COUNT = 752
TS_FIRST = 760
TS_STRIDE = 552
TS_LABEL, TS_ACTIVE, TS_REF = 0x00, 0x80, 0xC0
TS_LOWCUT, TS_HIGHCUT, TS_RATE, TS_RES = 0x100, 0x108, 0x110, 0x118
TS_MARK, TS_NOTCH = 0x120, 0x122
NAME_BYTES = 64  # wchar[32]

DERIV_GUID = "{8A19AA48-BEA0-40D5-B89F-667FC578D635}"
DERIV_NAME = 0x28
DERIV_COUNT = 744
DERIV_FIRST = 752
DERIV_STRIDE = 520  # same name fields as a channel record, shorter tail

SEG_STRIDE = 152
SEG_DATE, SEG_DURATION = 0x00, 0x10

EVT_LEN = 0x10
EVT_STAMP, EVT_DURATION = 0x20, 0x30
EVT_TYPE, EVT_CHANNEL, EVT_LABEL = 0x88, 0xA8, 0x108
EVT_MIN = 272  # a packet with an empty or short label

OLE_EPOCH = dt.datetime(1899, 12, 30)

# Nervus HCEVENT_* type GUIDs. Anything not listed is carried through as its
# raw GUID rather than dropped -- an unknown marker is still a marker.
EVENT_TYPES = {
    "{96315D79-5C24-4A65-B334-E31A95088D55}": "Exam Start",
    "{A5A95612-A7F8-11CF-831A-0800091B5BDA}": "Annotation",
    "{A5A95616-A7F8-11CF-831A-0800091B5BDA}": "Patient Event",
    "{A5A95608-A7F8-11CF-831A-0800091B5BDA}": "Seizure",
    "{A5A95611-A7F8-11CF-831A-0800091B5BDA}": "Hyperventilation",
    "{A5A95617-A7F8-11CF-831A-0800091B5BDA}": "Impedance",
    "{08784382-C765-11D3-90CE-00104B6F4F70}": "Format Change",
    "{6FF394DA-D1B8-46DA-B78F-866C67CF02AF}": "Photic",
    "{481DFC97-013C-4BC5-A203-871B0375A519}": "Post Hyperventilation",
    "{725798BF-CD1C-4909-B793-6C7864C27AB7}": "Review Progress",
    "{08EC3F49-978D-4FE4-AE77-4C421335E5FF}": "Prune",
}


def _guid(b):
    a, c, d = struct.unpack_from("<IHH", b, 0)
    return f"{{{a:08X}-{c:04X}-{d:04X}-{b[8:10].hex().upper()}-{b[10:16].hex().upper()}}}"


def _wstr(b, at, limit=None):
    """UTF-16LE up to the first NUL. `limit` caps a fixed-width field."""
    end = len(b) if limit is None else min(len(b), at + limit)
    raw = b[at:end]
    text = raw.decode("utf-16-le", "replace")
    return text.split("\x00")[0].strip()


def _ole(days):
    """OLE automation date. Rounded to the millisecond: the f64 carries ~0.1 us
    of noise at these magnitudes, which datetime would render as 47.000001 s."""
    return OLE_EPOCH + dt.timedelta(milliseconds=round(days * 86400000.0))


def _read_tags(fh):
    fh.seek(TAG_COUNT)
    n = struct.unpack("<I", fh.read(4))[0]
    fh.seek(TAG_FIRST)
    raw = fh.read(n * TAG_STRIDE)
    if len(raw) < n * TAG_STRIDE:
        raise EOFError("truncated tag table")
    tags = {}
    for i in range(n):
        at = i * TAG_STRIDE
        name = _wstr(raw, at, TAG_NAME)
        tags[struct.unpack_from("<I", raw, at + TAG_NAME)[0]] = name
    return tags


def _read_index(fh):
    fh.seek(INDEX_PTR)
    at = struct.unpack("<I", fh.read(4))[0]
    fh.seek(at)
    n = struct.unpack("<Q", fh.read(8))[0]
    raw = fh.read(n * IDX_STRIDE)
    if len(raw) < n * IDX_STRIDE:
        raise EOFError("truncated main index")
    sections, spans = {}, []
    for i in range(n):
        sec, off, block, data = struct.unpack_from("<QQII", raw, i * IDX_STRIDE)
        sections.setdefault(sec, []).append((off, data))
        spans.append(off + block)
    for entries in sections.values():
        entries.sort()
    return sections, max(spans)


def _read_channels(fh, packet):
    n = struct.unpack_from("<I", packet, TS_COUNT)[0]
    if n < 1 or TS_FIRST + n * TS_STRIDE > len(packet):
        raise ValueError(f"channel table declares {n} channels, packet holds {len(packet)} bytes")
    stride = (len(packet) - TS_FIRST) // n
    if stride != TS_STRIDE:
        raise ValueError(f"channel record is {stride} bytes, not {TS_STRIDE} -- newer .e variant?")
    out = []
    for i in range(n):
        at = TS_FIRST + i * TS_STRIDE
        rate, res = struct.unpack_from("<dd", packet, at + TS_RATE)
        out.append(dict(
            label=_wstr(packet, at + TS_LABEL, NAME_BYTES),
            active=_wstr(packet, at + TS_ACTIVE, NAME_BYTES),
            reference=_wstr(packet, at + TS_REF, NAME_BYTES),
            low_cut=struct.unpack_from("<d", packet, at + TS_LOWCUT)[0],
            high_cut=struct.unpack_from("<d", packet, at + TS_HIGHCUT)[0],
            sfreq=rate,
            resolution=res,
            mark=struct.unpack_from("<H", packet, at + TS_MARK)[0],
            notch=struct.unpack_from("<H", packet, at + TS_NOTCH)[0],
        ))
    return out


def _read_derivations(packet):
    """The viewer's display montage: a name, then one record per trace holding
    the same label/active/reference fields a channel record uses."""
    n = struct.unpack_from("<I", packet, DERIV_COUNT)[0]
    if DERIV_FIRST + n * DERIV_STRIDE != len(packet):
        raise ValueError(f"montage declares {n} derivations, packet holds {len(packet)} bytes")
    out = []
    for i in range(n):
        at = DERIV_FIRST + i * DERIV_STRIDE
        out.append(dict(
            label=_wstr(packet, at + TS_LABEL, NAME_BYTES),
            active=_wstr(packet, at + TS_ACTIVE, NAME_BYTES),
            reference=_wstr(packet, at + TS_REF, NAME_BYTES),
        ))
    return dict(name=_wstr(packet, DERIV_NAME, NAME_BYTES), channels=out)


def _read_segments(packet):
    if len(packet) % SEG_STRIDE:
        raise ValueError(f"SegmentStream is {len(packet)} bytes, not a multiple of {SEG_STRIDE}")
    out = []
    for at in range(0, len(packet), SEG_STRIDE):
        out.append(dict(
            start=_ole(struct.unpack_from("<d", packet, at + SEG_DATE)[0]),
            duration=struct.unpack_from("<d", packet, at + SEG_DURATION)[0],
        ))
    return out


def _read_events(packet):
    """Walk the Events section by each packet's own length field. Desync shows
    up immediately as a length outside the section, so it cannot run away."""
    out, at = [], 0
    while at + EVT_MIN <= len(packet):
        size = struct.unpack_from("<Q", packet, at + EVT_LEN)[0]
        if size < EVT_MIN or at + size > len(packet):
            raise ValueError(f"event packet at {at} declares {size} bytes")
        rec = packet[at:at + size]
        guid = _guid(rec[EVT_TYPE:EVT_TYPE + 16])
        out.append(dict(
            when=_ole(struct.unpack_from("<d", rec, EVT_STAMP)[0]),
            duration=struct.unpack_from("<d", rec, EVT_DURATION)[0],
            guid=guid,
            type=EVENT_TYPES.get(guid, guid),
            channel=_wstr(rec, EVT_CHANNEL, NAME_BYTES),
            label=_wstr(rec, EVT_LABEL),
        ))
        at += size
    if at != len(packet):
        raise ValueError(f"events walk consumed {at} of {len(packet)} bytes")
    return out


def _section(fh, sections, index):
    out = bytearray()
    for off, size in sections[index]:
        fh.seek(off)
        chunk = fh.read(size)
        if len(chunk) < size:
            raise EOFError(f"short read at {off}")
        out += chunk
    return bytes(out)


def read_header(path):
    """Everything but the samples. `channel_sections` maps channel number to a
    main-index key, for read_channel."""
    with open(path, "rb") as fh:
        if fh.read(16) != MAGIC:
            raise ValueError(f"{path} is not a Nicolet .e (bad magic GUID)")
        tags = _read_tags(fh)
        sections, end = _read_index(fh)

        fh.seek(0, 2)
        size = fh.tell()
        if end != size:
            raise ValueError(f"index spans end at {end}, file is {size} bytes")

        byname = {}
        for key, name in tags.items():
            if key in sections:
                byname.setdefault(name, key)
        for need in (TS_GUID, "SegmentStream"):
            if need not in byname:
                raise ValueError(f"no {need} section in {path}")

        channels = _read_channels(fh, _section(fh, sections, byname[TS_GUID]))
        segments = _read_segments(_section(fh, sections, byname["SegmentStream"]))
        events, montage = [], None
        if "Events" in byname:
            events = _read_events(_section(fh, sections, byname["Events"]))
        if DERIV_GUID in byname:
            montage = _read_derivations(_section(fh, sections, byname[DERIV_GUID]))

    channel_sections = {}
    for key, name in tags.items():
        if name.isdigit() and key in sections:
            channel_sections[int(name)] = key
    missing = set(range(len(channels))) - set(channel_sections)
    if missing:
        raise ValueError(f"no data section for channel(s) {sorted(missing)}")

    for ch in channels:  # samples must account for exactly the segment table
        want = sum(round(s["duration"] * ch["sfreq"]) for s in segments)
        ch["n_samples"] = want

    return dict(path=path, size=size, tags=tags, sections=sections,
                channel_sections=channel_sections, channels=channels,
                segments=segments, events=events, montage=montage)


def read_channel(fh, header, ch):
    """Raw little-endian int16 stream for one channel, segments back to back."""
    raw = _section(fh, header["sections"], header["channel_sections"][ch])
    want = header["channels"][ch]["n_samples"] * 2
    if len(raw) != want:
        raise ValueError(f"channel {ch}: {len(raw)} bytes, segment table wants {want}")
    return raw


def segment_bounds(header, sfreq):
    """(start, stop) sample index per segment for a channel at `sfreq`."""
    out, at = [], 0
    for seg in header["segments"]:
        n = round(seg["duration"] * sfreq)
        out.append((at, at + n))
        at += n
    return out


if __name__ == "__main__":
    import sys

    h = read_header(sys.argv[1])
    print(f"{h['path']}  {h['size']} bytes")
    print(f"{len(h['channels'])} channels, {len(h['segments'])} segments, "
          f"{len(h['events'])} events")
    for i, s in enumerate(h["segments"]):
        print(f"  seg {i}  {s['start']}  {s['duration']:g} s")
    for i, c in enumerate(h["channels"]):
        print(f"  ch {i:2d}  {c['label']:<8} {c['sfreq']:g} Hz  "
              f"{c['resolution']:.6f}  ref={c['reference'] or '-'}  n={c['n_samples']}")
    if h["montage"]:
        print(f"  montage {h['montage']['name']!r}")
        for m in h["montage"]["channels"]:
            print(f"    {m['label']:<14} {m['active']} - {m['reference'] or '(none)'}")
    for e in h["events"]:
        print(f"  {e['when']}  {e['duration']:8.2f}  {e['type']:<16} "
              f"{e['channel']:<10} {e['label']}")
