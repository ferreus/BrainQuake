"""Convert Nihon Kohden EEG-1200A (extended format) .EEG files to EDF+C.

The stock converters (nk2edf, MNE's read_raw_nihon) reject this header version
and would misparse the datablock even if the whitelist were patched -- the
EEG-1200A layout moved sfreq/duration/n_channels and widened them to u32.
See nk.py for the layout.

Usage:
  python nk2edf.py INPUT.EEG OUTDIR [--blocks 0,1,5-9] [--all-channels]
                                    [--ascii-labels] [--list]
"""
import argparse
import datetime as dt
import os
import sys

import numpy as np

import nk

REC_SECS = 1  # EDF data record duration
ANNOT_BYTES = 120  # bytes reserved for the EDF Annotations signal per record

EEG_PHYS_MAX = 3199.902  # uV, JE-120A/225A EEG inputs (+/-3200 uV full scale)
EEG_PHYS_MIN = -3200.0
DC_PHYS_MAX = 12002.9  # mV, DC inputs
DC_PHYS_MIN = -12002.9


def _fld(text, width):
    """Left-justified fixed-width ASCII EDF header field."""
    b = str(text).encode("ascii", "replace")[:width]
    return b + b" " * (width - len(b))


def _num(value, width):
    """EDF numeric field: as many significant digits as fit, no exponent."""
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    if len(s) > width:
        s = f"{value:.{max(0, width - len(str(int(value))) - 2)}f}"
        s = s.rstrip("0").rstrip(".")[:width]
    return _fld(s, width)


def build_header(ch_names, is_dc, n_records, sfreq, start, patient, recording):
    ns = len(ch_names) + 1  # + EDF Annotations
    h = bytearray()
    h += _fld("0", 8)
    h += _fld(patient, 80)
    h += _fld(recording, 80)
    h += _fld(start.strftime("%d.%m.%y"), 8)
    h += _fld(start.strftime("%H.%M.%S"), 8)
    h += _fld(256 * (ns + 1), 8)
    h += _fld("EDF+C", 44)
    h += _fld(n_records, 8)
    h += _fld(REC_SECS, 8)
    h += _fld(ns, 4)

    labels = list(ch_names) + ["EDF Annotations"]
    h += b"".join(_fld(x, 16) for x in labels)
    h += b"".join(_fld("", 80) for _ in range(ns))
    h += b"".join(
        _fld("mV" if is_dc[i] else "uV", 8) for i in range(len(ch_names))
    ) + _fld("", 8)
    h += b"".join(
        _num(DC_PHYS_MIN if is_dc[i] else EEG_PHYS_MIN, 8)
        for i in range(len(ch_names))
    ) + _fld("-1", 8)
    h += b"".join(
        _num(DC_PHYS_MAX if is_dc[i] else EEG_PHYS_MAX, 8)
        for i in range(len(ch_names))
    ) + _fld("1", 8)
    h += b"".join(_fld(-32768, 8) for _ in range(ns))
    h += b"".join(_fld(32767, 8) for _ in range(ns))
    h += b"".join(_fld("", 80) for _ in range(ns))
    h += b"".join(_fld(sfreq * REC_SECS, 8) for _ in ch_names)
    h += _fld(ANNOT_BYTES // 2, 8)
    h += b"".join(_fld("", 32) for _ in range(ns))
    assert len(h) == 256 * (ns + 1), len(h)
    return bytes(h)


def convert_block(eeg_path, blk, out_path, keep, patient, recording, ascii_labels):
    sfreq = blk["sfreq"]
    n_ch_file = blk["n_channels"]
    frame = n_ch_file + 1
    n_records = blk["n_samples"] // (sfreq * REC_SECS)

    names = [blk["ch_names"][i] for i in keep]
    if ascii_labels:
        names = [n.replace("'", "p") for n in names]
    # de-duplicate labels; EDF readers key on them
    seen = {}
    for i, n in enumerate(names):
        if n.strip() == "":
            n = f"UNUSED{blk['e21_index'][keep[i]]}"
        if n in seen:
            seen[n] += 1
            n = f"{n}-{seen[n]}"
        else:
            seen[n] = 0
        names[i] = n
    is_dc = [blk["e21_index"][i] in nk.DC_RANGE for i in keep]

    start = dt.datetime.strptime(blk["start"][:14], "%Y%m%d%H%M%S")
    header = build_header(
        names, is_dc, n_records, sfreq, start, patient, recording
    )

    # digital->physical is linear and identical for every channel of a kind,
    # so the raw NK code maps straight onto the EDF digital range: subtract the
    # 32768 offset and reinterpret as int16.
    keep_arr = np.asarray(keep, dtype=np.intp) + 1  # +1 skips the mark word

    chunk_records = max(1, 64 // REC_SECS)
    written = 0
    with open(eeg_path, "rb") as fin, open(out_path, "wb") as fout:
        fout.write(header)
        fin.seek(blk["data_address"])
        while written < n_records:
            nrec = min(chunk_records, n_records - written)
            nsamp = nrec * sfreq * REC_SECS
            buf = fin.read(nsamp * frame * 2)
            if len(buf) < nsamp * frame * 2:
                raise EOFError(f"short read in {blk['address']}")
            raw = np.frombuffer(buf, dtype="<u2").reshape(nsamp, frame)
            sig = (raw[:, keep_arr].astype(np.int32) - 32768).astype("<i2")
            sig = sig.reshape(nrec, sfreq * REC_SECS, len(keep))
            for r in range(nrec):
                # EDF record = per-signal contiguous blocks
                fout.write(np.ascontiguousarray(sig[r].T).tobytes())
                onset = (written + r) * REC_SECS
                tal = f"+{onset}\x14\x14\x00".encode("ascii")
                fout.write(tal + b"\x00" * (ANNOT_BYTES - len(tal)))
            written += nrec
    return n_records, len(names), start


def parse_sel(text, n):
    if text is None:
        return list(range(n))
    out = []
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    bad = [i for i in out if not 0 <= i < n]
    if bad:
        raise SystemExit(f"block index out of range: {bad}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("outdir", nargs="?")
    ap.add_argument("--blocks", help="e.g. 0,1,5-9 (default: all)")
    ap.add_argument(
        "--all-channels",
        action="store_true",
        help="keep the unconnected second-amp inputs too",
    )
    ap.add_argument("--ascii-labels", action="store_true", help="G'1 -> Gp1")
    ap.add_argument("--list", action="store_true", help="list blocks and exit")
    ap.add_argument("--patient", default="X X X X")
    args = ap.parse_args()

    blocks = nk.read_blocks(args.input)
    n_file_ch = blocks[0]["n_channels"]

    # Channels beyond the montage are unconnected inputs on the second amp
    # box: they mutually correlate at ~0.999 and carry only mains pickup.
    if args.all_channels:
        keep = list(range(n_file_ch))
    else:
        names = blocks[0]["ch_names"]
        idx = blocks[0]["e21_index"]
        last = max(i for i in range(n_file_ch) if idx[i] <= 253 and names[i])
        # the montage runs until the .21E index sequence restarts
        restart = next(
            (i for i in range(1, n_file_ch) if idx[i] < idx[i - 1]), n_file_ch
        )
        keep = list(range(min(last + 1, restart)))

    if args.list:
        print(f"{len(blocks)} datablocks in {args.input}")
        print(f"{n_file_ch} channels in file, exporting {len(keep)}")
        print()
        print(f"{'#':>3} {'start':>20} {'dur(s)':>8} {'MB out':>8}")
        for i, b in enumerate(blocks):
            mb = b["n_samples"] * len(keep) * 2 / 1e6
            print(f"{i:3d} {b['start'][:14]:>20} {b['duration']:8.1f} {mb:8.1f}")
        tot = sum(b["n_samples"] for b in blocks) * len(keep) * 2 / 1e9
        print(f"\ntotal EDF output: {tot:.2f} GB")
        return

    if not args.outdir:
        raise SystemExit("outdir required (or use --list)")
    os.makedirs(args.outdir, exist_ok=True)
    sel = parse_sel(args.blocks, len(blocks))
    stem = os.path.splitext(os.path.basename(args.input))[0]

    for i in sel:
        b = blocks[i]
        out = os.path.join(args.outdir, f"{stem}_{i:02d}_{b['start'][:14]}.edf")
        # EDF+ recording field is strictly
        # "Startdate dd-MMM-yyyy <admin code> <investigator> <equipment>"
        d = dt.datetime.strptime(b["start"][:8], "%Y%m%d")
        mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"][d.month - 1]
        recording = f"Startdate {d.day:02d}-{mon}-{d.year} {stem} X JE-120A/225A"
        nrec, nch, start = convert_block(
            args.input, b, out, keep, args.patient, recording,
            args.ascii_labels,
        )
        size = os.path.getsize(out)
        print(
            f"[{i:2d}/{len(blocks)-1}] {os.path.basename(out)}  "
            f"{nch} ch  {nrec} s  {size/1e6:.1f} MB",
            flush=True,
        )


if __name__ == "__main__":
    main()
