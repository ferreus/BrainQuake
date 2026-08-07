"""Nihon Kohden EEG-1200A (extended format) reader.

Layout verified against DA6465AU.EEG: the datablock chain walks byte-exact
to EOF with zero remainder.

datablock header:
  +0x00  u8    0x01
  +0x01  16s   "TIMEhhmmss000000"
  +0x14  20s   "YYYYMMDDhhmmss000000"
  +0x28  u32   sampling rate (Hz)
  +0x2C  u32   duration in units of 0.1 s
  +0x44  u32   n_channels
  +0x48  ...   channel table, 10 bytes/entry, byte 0 = index into .21E
  then          n_samples frames of (n_channels + 1) little-endian u16;
                word 0 of each frame is the mark/event word.
"""
import os
import struct

# JE-120A/225A: EEG inputs are +/-3200 uV over the 16-bit range.
UV_PER_LSB = 6400.0 / 65536.0  # = 0.09765625
DC_UV_PER_LSB = 12002.9 * 1000.0 * 2 / 65536.0  # DC inputs are +/-12002.9 mV

# .21E indices that are DC / non-EEG inputs (per NK channel map)
DC_RANGE = set(range(42, 74))


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


def read_blocks(eeg_path, first=17403):
    """Walk the datablock chain. Returns a list of block dicts."""
    size = os.path.getsize(eeg_path)
    labels = read_21e(_e21_path(eeg_path))
    blocks = []
    addr = first
    with open(eeg_path, "rb") as fh:
        while addr < size:
            fh.seek(addr)
            head = fh.read(0x48)
            if len(head) < 0x48 or head[0] != 1 or head[1:5] != b"TIME":
                raise ValueError(f"datablock chain broke at {addr}")
            stamp = head[0x14:0x28].decode("latin-1")
            sfreq = struct.unpack_from("<I", head, 0x28)[0]
            dur10 = struct.unpack_from("<I", head, 0x2C)[0]
            n_ch = struct.unpack_from("<I", head, 0x44)[0]
            table = fh.read(n_ch * 10)
            idx = [table[i * 10] for i in range(n_ch)]
            n_samp = dur10 * sfreq // 10
            data_at = addr + 0x48 + n_ch * 10
            blocks.append(
                dict(
                    address=addr,
                    data_address=data_at,
                    start=stamp,
                    sfreq=sfreq,
                    duration=dur10 / 10.0,
                    n_channels=n_ch,
                    n_samples=n_samp,
                    e21_index=idx,
                    ch_names=[labels.get(i, f"#{i}") for i in idx],
                )
            )
            addr = data_at + n_samp * (n_ch + 1) * 2
    if addr != size:
        raise ValueError(f"chain ended at {addr}, file is {size}")
    return blocks


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
