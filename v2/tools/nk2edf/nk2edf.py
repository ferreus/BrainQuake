"""Convert Nihon Kohden EEG-1200A (extended format) .EEG files to EDF+C.

The stock converters (nk2edf, MNE's read_raw_nihon) reject this header version
and would misparse the datablock even if the whitelist were patched -- the
EEG-1200A layout moved sfreq/duration/n_channels and widened them to u32.
See nk.py for the layout.

Events from the .LOG and the per-sample mark word are written as EDF+
annotations and a MARK signal respectively -- both were previously discarded.
The .LOG layout is unverified: run --dump-log and check it before trusting a
converted file. See README.md.

Usage:
  python nk2edf.py INPUT.EEG OUTDIR [--blocks 0,1,5-9] [--all-channels]
                                    [--ascii-labels] [--list] [--dump-log]
                                    [--log PATH] [--no-log] [--annotations CSV]
                                    [--no-mark-channel]
"""
import argparse
import datetime as dt
import os

import nk
import numpy as np

REC_SECS = 1  # EDF data record duration
MIN_ANNOT_BYTES = 120  # floor for the EDF Annotations signal, per record

EEG_PHYS_MAX = 3199.902  # uV, JE-120A/225A EEG inputs (+/-3200 uV full scale)
EEG_PHYS_MIN = -3200.0
DC_PHYS_MAX = 12002.9  # mV, DC inputs
DC_PHYS_MIN = -12002.9

# The mark/event word is a raw code, not a measurement, so it is written with
# physical == digital and no unit. Its label deliberately does not look like an
# SEEG contact (one letter, optional prime, a number), which is how the server
# separates contacts from auxiliary traces.
MARK_LABEL = "MARK"


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


def build_header(signals, n_records, sfreq, start, patient, recording, annot_bytes):
    """`signals` is a list of (label, unit, phys_min, phys_max) for the data
    signals; the EDF Annotations signal is appended here."""
    ns = len(signals) + 1  # + EDF Annotations
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

    h += b"".join(_fld(s[0], 16) for s in signals) + _fld("EDF Annotations", 16)
    h += b"".join(_fld("", 80) for _ in range(ns))
    h += b"".join(_fld(s[1], 8) for s in signals) + _fld("", 8)
    h += b"".join(_num(s[2], 8) for s in signals) + _fld("-1", 8)
    h += b"".join(_num(s[3], 8) for s in signals) + _fld("1", 8)
    h += b"".join(_fld(-32768, 8) for _ in range(ns))
    h += b"".join(_fld(32767, 8) for _ in range(ns))
    h += b"".join(_fld("", 80) for _ in range(ns))
    h += b"".join(_fld(sfreq * REC_SECS, 8) for _ in signals)
    h += _fld(annot_bytes // 2, 8)
    h += b"".join(_fld("", 32) for _ in range(ns))
    assert len(h) == 256 * (ns + 1), len(h)
    return bytes(h)


def _tal(onset, text=None):
    """One EDF+ Time-stamped Annotation List entry."""
    head = f"+{onset:.3f}".rstrip("0").rstrip(".")
    if text is None:  # the timekeeping TAL every record must start with
        return (head + "\x14\x14\x00").encode("utf-8")
    clean = text.replace("\x14", " ").replace("\x00", " ").strip()
    return (head + "\x14" + clean + "\x14\x00").encode("utf-8")


def plan_annotations(events, n_records):
    """Group events into the record containing their onset, and size the
    annotation signal so the fullest record fits. Returns (per_record, nbytes)."""
    per_record = {}
    for onset, text in events:
        r = min(int(onset // REC_SECS), n_records - 1)
        per_record.setdefault(r, []).append(_tal(onset, text))
    widest = 0
    for r in range(n_records):
        used = len(_tal(r * REC_SECS)) + sum(len(t) for t in per_record.get(r, ()))
        widest = max(widest, used)
    nbytes = max(MIN_ANNOT_BYTES, widest + (widest % 2))
    return per_record, nbytes


def events_for_block(log_events, start, duration):
    """Map absolute log timestamps onto one clip's timeline.

    Matching on wall-clock time is what makes this safe: the Nihon Kohden
    viewer's "elapsed" counter is cumulative across every clip in the study,
    not time within a clip, so anything derived from it needs a per-clip offset
    to be worked out by hand. Each datablock carries its own absolute start, so
    an event either falls inside a clip or it does not.
    """
    out = []
    for ev in log_events:
        when = ev["when"]
        if isinstance(when, dt.datetime):
            moment = when
        else:  # bare hh:mm:ss -- resolve against this clip's date, allowing midnight
            moment = dt.datetime.combine(start.date(), when)
            if moment < start - dt.timedelta(hours=12):
                moment += dt.timedelta(days=1)
            elif moment > start + dt.timedelta(hours=12):
                moment -= dt.timedelta(days=1)
        onset = (moment - start).total_seconds()
        if 0 <= onset < duration:
            out.append((onset, ev["text"]))
    return sorted(out)


def convert_block(eeg_path, blk, out_path, keep, patient, recording, ascii_labels,
                  events=(), keep_mark=True):
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

    signals = [
        (names[i],
         "mV" if is_dc[i] else "uV",
         DC_PHYS_MIN if is_dc[i] else EEG_PHYS_MIN,
         DC_PHYS_MAX if is_dc[i] else EEG_PHYS_MAX)
        for i in range(len(names))
    ]
    if keep_mark:
        signals.append((MARK_LABEL, "", -32768, 32767))

    per_record, annot_bytes = plan_annotations(events, n_records)
    header = build_header(
        signals, n_records, sfreq, start, patient, recording, annot_bytes
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
            cols = raw[:, keep_arr]
            if keep_mark:
                # Word 0 of every frame. Previously discarded outright, which
                # threw away the hardware event line (patient button, technician
                # marks) with no way to recover it short of re-converting.
                cols = np.concatenate([cols, raw[:, :1]], axis=1)
            sig = (cols.astype(np.int32) - 32768).astype("<i2")
            sig = sig.reshape(nrec, sfreq * REC_SECS, cols.shape[1])
            for r in range(nrec):
                # EDF record = per-signal contiguous blocks
                fout.write(np.ascontiguousarray(sig[r].T).tobytes())
                rec_i = written + r
                tals = _tal(rec_i * REC_SECS) + b"".join(per_record.get(rec_i, ()))
                if len(tals) > annot_bytes:  # plan_annotations sized this
                    raise AssertionError(f"record {rec_i}: {len(tals)} > {annot_bytes} annotation bytes")
                fout.write(tals + b"\x00" * (annot_bytes - len(tals)))
            written += nrec
    return n_records, len(names), start


def default_log_path(eeg_path):
    base = os.path.splitext(eeg_path)[0]
    for cand in (base + ".LOG", base + ".log"):
        if os.path.exists(cand):
            return cand
    return None


def read_annotation_csv(path):
    """Sidecar events: `<when>,<text>` per line, `#` comments ignored.

    `when` is either an ISO datetime (absolute, matched to clips the same way
    log entries are) or a bare number of seconds from the start of the clip --
    the latter only being meaningful when a single block is being converted.
    """
    events = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            when, _, text = line.partition(",")
            if not text.strip():
                raise SystemExit(f"{path}:{lineno}: expected '<when>,<text>'")
            when = when.strip()
            try:
                events.append({"when": dt.datetime.fromisoformat(when), "text": text.strip()})
            except ValueError:
                try:
                    events.append({"seconds": float(when), "text": text.strip()})
                except ValueError:
                    raise SystemExit(
                        f"{path}:{lineno}: {when!r} is neither an ISO datetime nor a number of seconds"
                    ) from None
    return events


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
    ap.add_argument("--log", help="Nihon Kohden .LOG (default: alongside the .EEG)")
    ap.add_argument("--no-log", action="store_true", help="ignore the .LOG entirely")
    ap.add_argument("--annotations", help="sidecar CSV of '<iso datetime|seconds>,<text>'")
    ap.add_argument(
        "--dump-log",
        action="store_true",
        help="print the parsed log events per clip and exit -- check these against "
             "the Nihon Kohden viewer before trusting a converted file",
    )
    ap.add_argument(
        "--no-mark-channel",
        action="store_true",
        help="drop the per-sample mark/event word instead of writing it as a MARK signal",
    )
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

    log_events = []
    log_path = args.log or (None if args.no_log else default_log_path(args.input))
    if log_path:
        log_events = nk.read_log(log_path)
        print(f"{os.path.basename(log_path)}: {len(log_events)} log events")
    elif not args.no_log:
        print("no .LOG found next to the .EEG -- converting without log annotations")
    if args.annotations:
        log_events += read_annotation_csv(args.annotations)

    if args.dump_log:
        placed = 0
        for i, b in enumerate(blocks):
            start = dt.datetime.strptime(b["start"][:14], "%Y%m%d%H%M%S")
            evs = events_for_block(log_events, start, b["duration"])
            placed += len(evs)
            if evs:
                print(f"\nblock {i}  {start}  ({b['duration']:.1f}s)")
                for onset, text in evs:
                    print(f"  {onset:9.3f}  {text}")
        print(f"\n{placed}/{len(log_events)} events fell inside a clip")
        if log_events and not placed:
            print(
                "\nNone of them landed in a clip. Either the log covers a different\n"
                "recording, or nk.read_log()'s entry layout is wrong -- it is the one\n"
                "part of this format that was never verified against a reference file."
            )
        return

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
        block_start = dt.datetime.strptime(b["start"][:14], "%Y%m%d%H%M%S")
        events = events_for_block(
            [e for e in log_events if "when" in e], block_start, b["duration"]
        )
        relative = [(e["seconds"], e["text"]) for e in log_events if "seconds" in e]
        if relative:
            if len(sel) != 1:
                raise SystemExit(
                    "--annotations entries given in seconds are ambiguous when converting "
                    "more than one block; use ISO datetimes or --blocks with a single index"
                )
            events += [(s, t) for s, t in relative if 0 <= s < b["duration"]]
        events.sort()

        nrec, nch, start = convert_block(
            args.input, b, out, keep, args.patient, recording,
            args.ascii_labels, events=events, keep_mark=not args.no_mark_channel,
        )
        size = os.path.getsize(out)
        print(
            f"[{i:2d}/{len(blocks)-1}] {os.path.basename(out)}  "
            f"{nch} ch  {nrec} s  {len(events)} annot  {size/1e6:.1f} MB",
            flush=True,
        )


if __name__ == "__main__":
    main()
