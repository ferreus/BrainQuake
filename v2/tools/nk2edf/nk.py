"""Nihon Kohden reader: EEG-1100 series (legacy) and EEG-1200A (extended).

Datablock addresses come from the file's own tables -- nothing here is a
fixed offset except the header fields below.

  0x91 / 0x92           legacy control block count (u8) + address (u32)
  <ctl> + 0x11 / 0x12   legacy datablock count (u8) + addresses (u32, stride 20)

EEG-1200A adds a second table after the legacy datablock, which it degrades to
a 1-channel stub:

  <legacy block end>    "EEG-1200A V01.00" at +0x01, u8 count at +0x11,
                        extended control addresses at +0x12, stride 20
  <ext ctl> + 0x11      u16 datablock count
  <ext ctl> + 0x14      u64 datablock addresses, stride 24 (files exceed 4 GB)

datablock header, legacy | extended:
  +0x00  u8   | u8    0x01
  +0x01  16s  | 16s   "TIMEhhmmss000000"
  +0x14  6 B  | 20s   BCD YY MM DD hh mm ss | ASCII "YYYYMMDDhhmmss000000"
  +0x1A  u16  |       sampling rate, mask 0x3FFF
  +0x1C  u32  |       duration in units of 0.1 s
  +0x26  u8   |       n_channels
  +0x27  ...  |       channel table, 10 bytes/entry, byte 0 = index into .21E
  +0x28       | u32   sampling rate (Hz)
  +0x2C       | u32   duration in units of 0.1 s
  +0x44       | u32   n_channels
  +0x48       | ...   channel table, 10 bytes/entry

Then n_samples frames of (n_channels + 1) little-endian u16. Words 0..n-1 are
the channels in table order, offset-binary (subtract 32768). The **last** word
is the event/marker word and is NOT offset-binary -- EDFbrowser's nk2edf.cpp
reads `channels = n_ch + 1`, applies its high-byte fixup to the first
`channels - 1` words only, and emits the last one as "Events/Markers".
"""
import datetime as dt
import os
import re
import struct

# JE-120A/225A: EEG inputs are +/-3200 uV over the 16-bit range.
UV_PER_LSB = 6400.0 / 65536.0  # = 0.09765625
DC_UV_PER_LSB = 12002.9 * 1000.0 * 2 / 65536.0  # DC inputs are +/-12002.9 mV

# .21E indices carrying the DC scale. 76/77 are the mark inputs and take it too
# (nk2edf.cpp:1262 excludes them from the uV branch alongside 42-73).
DC_RANGE = set(range(42, 74)) | {76, 77}

_FIXED_LABELS = {
    0: "EEG FP1", 1: "EEG FP2", 2: "EEG F3", 3: "EEG F4", 4: "EEG C3",
    5: "EEG C4", 6: "EEG P3", 7: "EEG P4", 8: "EEG O1", 9: "EEG O2",
    10: "EEG F7", 11: "EEG F8", 12: "EEG T3", 13: "EEG T4", 14: "EEG T5",
    15: "EEG T6", 16: "EEG FZ", 17: "EEG CZ", 18: "EEG PZ", 19: "EEG E",
    20: "EEG PG1", 21: "EEG PG2", 22: "EEG A1", 23: "EEG A2", 24: "EEG T1",
    25: "EEG T2", 74: "EEG BN1", 75: "EEG BN2", 76: "EEG Mark1",
    77: "EEG Mark2", 100: "EEG X12/BP1", 101: "EEG X13/BP2",
    102: "EEG X14/BP3", 103: "EEG X15/BP4", 255: "Z",
}


def fallback_label(i):
    """Nihon Kohden's built-in name for a .21E index, used where the .21E has
    no entry. Mirrors the table in EDFbrowser's nk2edf.cpp:416-470."""
    if i in _FIXED_LABELS:
        return _FIXED_LABELS[i]
    if 26 <= i <= 36:
        return f"EEG X{i - 25}"
    if 42 <= i <= 73:
        return f"DC{i - 41:02d}"
    if 104 <= i <= 253:
        return f"EEG X{i - 88}"
    return None


def read_21e(path):
    labels = {}
    section = None
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("["):
                section = line.upper()
                continue
            if section != "[ELECTRODE]" or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if key.isdigit():
                labels.setdefault(int(key), val.strip())
    return labels


def _e21_path(eeg_path):
    base = os.path.splitext(eeg_path)[0]
    for cand in (base + ".21E", base + ".21e"):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(f"no .21E next to {eeg_path}")


EXT_MARKER = b"EEG-1200A V01.00"


def _u8(fh, at):
    fh.seek(at)
    return fh.read(1)[0]


def _u16(fh, at):
    fh.seek(at)
    return struct.unpack("<H", fh.read(2))[0]


def _u32(fh, at):
    fh.seek(at)
    return struct.unpack("<I", fh.read(4))[0]


def _version(fh):
    fh.seek(0)
    return fh.read(16).decode("latin-1").strip()


def _name(labels, i):
    """.21E name, else Nihon Kohden's built-in one. A present-but-empty .21E
    entry does not win -- EDFbrowser only overrides its table when the value is
    non-empty (nk2edf.cpp read_21e_file, `if(n > 0)`)."""
    return (labels.get(i) or "").strip() or fallback_label(i) or f"#{i}"


def _legacy_addresses(fh):
    """Datablock addresses from the legacy control table."""
    out = []
    for i in range(_u8(fh, 0x91)):
        ctl = _u32(fh, 0x92 + i * 20)
        for j in range(_u8(fh, ctl + 0x11)):
            out.append(_u32(fh, ctl + 0x12 + j * 20))
    return out


def _legacy_block_end(fh):
    """End of the last legacy datablock -- where an EEG-1200A extension starts."""
    addrs = _legacy_addresses(fh)
    if not addrs:
        raise ValueError("no legacy datablock")
    addr = addrs[-1]
    sfreq = _u16(fh, addr + 0x1A) & 0x3FFF
    dur10 = _u32(fh, addr + 0x1C)
    n_ch = _u8(fh, addr + 0x26)
    return addr + 0x27 + n_ch * 10 + (dur10 * sfreq // 10) * (n_ch + 1) * 2


def _extension_at(fh):
    """Offset of the EEG-1200A extension header, or None for a plain 1100."""
    ext = _legacy_block_end(fh)
    fh.seek(ext + 1)
    return ext if fh.read(16) == EXT_MARKER else None


def block_addresses(fh, ext):
    """Addresses of every extended datablock, in file order."""
    out = []
    for i in range(_u8(fh, ext + 0x11)):
        ctl = _u32(fh, ext + 0x12 + i * 20)
        n = _u16(fh, ctl + 0x11)
        fh.seek(ctl + 0x14)
        table = fh.read(n * 24)
        out += [struct.unpack_from("<Q", table, j * 24)[0] for j in range(n)]
    return out


def _read_legacy_block(fh, addr, labels):
    fh.seek(addr)
    head = fh.read(0x27)
    if len(head) < 0x27 or head[0] != 1 or head[1:5] != b"TIME":
        raise ValueError(f"no datablock at {addr}")
    # BCD YY MM DD hh mm ss -- hex-formatting a BCD byte prints its digits.
    stamp = "20" + "".join(f"{b:02x}" for b in head[0x14:0x1A]) + "000000"
    sfreq = struct.unpack_from("<H", head, 0x1A)[0] & 0x3FFF
    dur10 = struct.unpack_from("<I", head, 0x1C)[0]
    n_ch = head[0x26]
    return _block(fh, addr, addr + 0x27, stamp, sfreq, dur10, n_ch, labels)


def _read_extended_block(fh, addr, labels):
    fh.seek(addr)
    head = fh.read(0x48)
    if len(head) < 0x48 or head[0] != 1 or head[1:5] != b"TIME":
        raise ValueError(f"no datablock at {addr}")
    stamp = head[0x14:0x28].decode("latin-1")
    sfreq = struct.unpack_from("<I", head, 0x28)[0]
    dur10 = struct.unpack_from("<I", head, 0x2C)[0]
    n_ch = struct.unpack_from("<I", head, 0x44)[0]
    return _block(fh, addr, addr + 0x48, stamp, sfreq, dur10, n_ch, labels)


def _block(fh, addr, table_at, stamp, sfreq, dur10, n_ch, labels):
    fh.seek(table_at)
    table = fh.read(n_ch * 10)
    idx = [table[i * 10] for i in range(n_ch)]
    return dict(
        address=addr,
        data_address=table_at + n_ch * 10,
        start=stamp,
        sfreq=sfreq,
        duration=dur10 / 10.0,
        n_channels=n_ch,
        n_samples=dur10 * sfreq // 10,
        e21_index=idx,
        ch_names=[_name(labels, i) for i in idx],
    )


def read_blocks(eeg_path):
    """Read every datablock listed in the file's tables. Returns a list of dicts.

    EEG-1200A files are read from their extended table; everything else falls
    back to the legacy EEG-1100 control table."""
    labels = read_21e(_e21_path(eeg_path))
    with open(eeg_path, "rb") as fh:
        ext = _extension_at(fh)
        if ext is None:
            return [_read_legacy_block(fh, a, labels) for a in _legacy_addresses(fh)]
        return [_read_extended_block(fh, a, labels) for a in block_addresses(fh, ext)]


# An entry's timestamp field: "EEEEEE(YYMMDDhhmmss)" -- elapsed, then absolute.
_STAMP = re.compile(rb"(\d{6})\((\d{12})\)")


def _parse_stamp(buf):
    """(absolute datetime, elapsed seconds) from an entry's timestamp field.

    The field is exactly 20 bytes, "EEEEEE(YYMMDDhhmmss)":

      EEEEEE          the viewer's *elapsed* counter, as HHMMSS. This is
                      cumulative recorded time across every clip in the study,
                      NOT time within a clip -- it is what the Nihon Kohden
                      viewer displays, which is why an event shown at "29:38"
                      is not 29:38 into any file.
      YYMMDDhhmmss    absolute wall clock, 2-digit year.

    Only the absolute value can be mapped onto a clip; the elapsed value is
    returned for diagnostics (--dump-log) so the two can be cross-checked.
    """
    m = _STAMP.search(buf)
    if not m:
        return None, None
    try:
        when = dt.datetime.strptime(m.group(2).decode(), "%y%m%d%H%M%S")
    except ValueError:
        return None, None
    e = m.group(1).decode()
    elapsed = int(e[:2]) * 3600 + int(e[2:4]) * 60 + int(e[4:6])
    return when, elapsed


_MICROS = re.compile(rb"\d{4}(\d{6})\d{4}\1")


def _sub_block(data, i, n_entries):
    """Address of log block i's microsecond table, or None if the file has none.

    Same block shape as the main log table at 0x92, addressed from 0x24A: count
    at +0x12, then 45-byte entries parallel to the log's own. EEG-1100 files
    have no such table, and then the whole-second stamp is all there is.
    """
    off = 0x24A + i * 20
    if off + 4 > len(data):
        return None
    (addr,) = struct.unpack_from("<I", data, off)
    if not (0 < addr < len(data) - 0x14) or data[addr + 0x12] != n_entries:
        return None
    return addr


def _entry_micros(data, addr, j):
    """Entry j's sub-second part, as "<4 digits><micros><4 digits><micros>".

    Writing the value twice is what makes it self-checking: a record that does
    not repeat is not this field, and the entry keeps its whole-second stamp.
    """
    lo = addr + 0x14 + j * 45 + 20
    m = _MICROS.fullmatch(data[lo : lo + 20])
    return int(m.group(1)) if m else 0


def read_log(log_path):
    """Parse a Nihon Kohden .LOG into [{'when', 'elapsed', 'text'}] entries.

    Verified against DA6465AU.LOG (EEG-1200A): recovers all 406 entries, and the
    block/entry arithmetic is self-consistent -- block 0 declares 255 entries and
    the 11495 bytes to block 1 hold exactly 255 * 45.

    Layout (same block-table shape as the .EEG control block):
      0x91            u8    number of log blocks
      0x92 + i*20     u32   address of log block i, then a 16-byte name
      <block> + 0x12  u8    number of entries in the block
      <block> + 0x14  45 B  per entry: 20 bytes text, 20 bytes timestamp, 5 pad

    The block count sits at 0x91, not 0x92; reading it one byte late yields 0
    and silently produces no events at all.

    A stamp only resolves to the second. The sub-second part lives in a second
    table of the same shape (see _sub_block), one entry per log entry -- without
    it a mark lands up to half a second from where the viewer draws it.
    """
    with open(log_path, "rb") as fh:
        data = fh.read()
    if len(data) < 0x94:
        raise ValueError(f"{log_path} is too short to be a Nihon Kohden log")

    events = []
    n_blocks = data[0x91]
    for i in range(n_blocks):
        off = 0x92 + i * 20
        if off + 4 > len(data):
            break
        (addr,) = struct.unpack_from("<I", data, off)
        if not (0 < addr < len(data) - 0x14):
            continue
        n_entries = data[addr + 0x12]
        sub = _sub_block(data, i, n_entries)
        for j in range(n_entries):
            lo = addr + 0x14 + j * 45
            rec = data[lo : lo + 45]
            if len(rec) < 45:
                break
            # The text field carries stray control bytes (record flags bleeding
            # in from the preceding entry); keep printable ASCII only.
            text = "".join(
                c for c in rec[:20].decode("latin-1") if 0x20 <= ord(c) < 0x7F
            ).strip()
            when, elapsed = _parse_stamp(rec[20:])
            if text and when is not None:
                if sub:
                    when += dt.timedelta(microseconds=_entry_micros(data, sub, j))
                events.append({"when": when, "elapsed": elapsed, "text": text})
    return events


if __name__ == "__main__":
    import sys

    blocks = read_blocks(sys.argv[1])
    b = blocks[0]
    print(f"{len(blocks)} datablocks, {b['n_channels']} ch @ {b['sfreq']} Hz")
    print(f"total {sum(x['duration'] for x in blocks)/3600:.2f} h")
    print()
    print("e21 indices:", b["e21_index"])
    print()
    for i in range(0, b["n_channels"], 8):
        row = b["ch_names"][i : i + 8]
        print(f"{i:3d}: " + " ".join(f"{n:>9}" for n in row))
