"""Convert Nihon Kohden .EEG files to EDF+C: EEG-1200A and the EEG-1100 series.

The stock converters reject the EEG-1200A header version, and would misparse
the datablock even if the whitelist were patched -- the extended layout moved
sfreq/duration/n_channels and widened them to u32. EEG-1100 files are the
legacy layout that EDFbrowser's nk2edf handles; ours is checked against it.
See nk.py for both layouts.

Events from the .LOG and the per-frame event word are written as EDF+
annotations and an Events/Markers signal respectively -- both were previously
discarded.
The .LOG layout is unverified: run --dump-log and check it before trusting a
converted file. See README.md.

Every sidecar sharing the .EEG's stem is discovered and used (see nkmeta):
montages from .PTN, DC channel units from .11D, sex/birth date from .PNT,
clinician bookmarks from .sld. Any of them may be absent.

Usage:
  python nk2edf.py INPUT.EEG OUTDIR [--blocks 0,1,5-9] [--all-channels]
                                    [--ascii-labels] [--list] [--dump-log]
                                    [--log PATH] [--no-log] [--annotations CSV]
                                    [--no-mark-channel] [--montage NAME|auto]
                                    [--no-sidecar]
"""
import argparse
import datetime as dt
import json
import os
import re

import nk
import nkmeta
import numpy as np

REC_SECS = 1  # EDF data record duration
MIN_ANNOT_BYTES = 120  # floor for the EDF Annotations signal, per record

EEG_PHYS_MAX = 3199.902  # uV, EEG inputs (+/-3200 uV full scale)
EEG_PHYS_MIN = -3200.0
DC_PHYS_MAX = 12002.56  # mV, DC inputs; both match EDFbrowser's nk2edf exactly
DC_PHYS_MIN = -12002.9

# The event word is a raw code, not a measurement. EDFbrowser names it exactly
# this and gives it physical -1..1 over the full digital range; keeping the name
# lets converted files be diffed against its output. It also does not look like
# an SEEG contact (one letter, optional prime, a number), which is how the
# server separates contacts from auxiliary traces.
EVENT_LABEL = "Events/Markers"

# A bipolar trace spans twice the single-ended range, so montage output is
# halved into the int16 digital range rather than clipped.
BIPOLAR_PHYS_MAX = (EEG_PHYS_MAX - EEG_PHYS_MIN) / 2

_PLACEHOLDER = re.compile(r"^#\d+$")


def dc_key(e21):
    """.11D [ConvertDisplay] prefix for a DC input -- DC01 is .21E index 42."""
    return f"DC{e21 - 41:02d}" if 42 <= e21 <= 73 else None


def select_channels(blk, dc_cal, e21_labels):
    """Channels the recording actually carries: DC inputs only where the .11D
    enables them, and nothing whose .21E entry exists but is blank -- that is
    the recording stating no electrode is connected there."""
    keep = []
    for i, e in enumerate(blk["e21_index"]):
        if e in e21_labels and not e21_labels[e].strip():
            continue
        if _PLACEHOLDER.match(blk["ch_names"][i]):
            continue
        cal = dc_cal.get(dc_key(e) or "")
        if cal is not None and not cal["enable"]:
            continue
        keep.append(i)
    return keep


def signal_spec(label, e21, dc_cal):
    """(label, unit, phys_min, phys_max) for one data signal."""
    cal = dc_cal.get(dc_key(e21) or "")
    if cal and cal.get("unit") and cal["coefficient"]:
        c, o = cal["coefficient"], cal["offset"]
        lo, hi = DC_PHYS_MIN * c + o, DC_PHYS_MAX * c + o
        return (label, cal["unit"], min(lo, hi), max(lo, hi))
    if e21 in nk.DC_RANGE:
        return (label, "mV", DC_PHYS_MIN, DC_PHYS_MAX)
    return (label, "uV", EEG_PHYS_MIN, EEG_PHYS_MAX)


def montage_columns(montage, blk):
    """Map a montage onto (a, b) file-channel positions.

    An electrode the recording does not carry -- notably the 0V reference --
    contributes zero, which is what makes `0V-EKG1` come out as -EKG1.
    """
    pos = {e: i for i, e in enumerate(blk["e21_index"])}
    out = []
    for ch in montage["channels"]:
        a, b = pos.get(ch["a"]), pos.get(ch["b"])
        if a is None and b is None:
            continue
        out.append((ch["label"], a, b))
    return out


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
                  events=(), keep_mark=True, dc_cal=None, montage=None):
    dc_cal = dc_cal or {}
    sfreq = blk["sfreq"]
    n_ch_file = blk["n_channels"]
    frame = n_ch_file + 1
    n_records = blk["n_samples"] // (sfreq * REC_SECS)

    # One code path for both outputs: every signal is a pair (a, b) of file
    # channel positions, and referential output simply has no b.
    if montage:
        cols = montage_columns(montage, blk)
        names = [c[0] for c in cols]
        pairs = [(c[1], c[2]) for c in cols]
    else:
        names = [blk["ch_names"][i] for i in keep]
        pairs = [(i, None) for i in keep]
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

    start = dt.datetime.strptime(blk["start"][:14], "%Y%m%d%H%M%S")

    if montage:
        signals = [(n, "uV", -BIPOLAR_PHYS_MAX, BIPOLAR_PHYS_MAX) for n in names]
    else:
        signals = [
            signal_spec(names[i], blk["e21_index"][keep[i]], dc_cal)
            for i in range(len(names))
        ]
    if keep_mark:
        signals.append((EVENT_LABEL, "", -1, 1))

    per_record, annot_bytes = plan_annotations(events, n_records)
    header = build_header(
        signals, n_records, sfreq, start, patient, recording, annot_bytes
    )

    # digital->physical is linear and identical for every channel of a kind, so
    # the raw NK code maps straight onto the EDF digital range: subtract the
    # 32768 offset and reinterpret as int16. Channels are words 0..n-1 in table
    # order; an absent side reads word 0 and is zeroed out.
    def _side(k):
        idx = np.array([p[k] if p[k] is not None else 0 for p in pairs],
                       dtype=np.intp)
        on = np.array([p[k] is not None for p in pairs], dtype=np.int32)
        return idx, on

    a_idx, a_on = _side(0)
    b_idx, b_on = _side(1)

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
            sig = (raw[:, a_idx].astype(np.int32) - 32768) * a_on
            if montage:
                sig -= (raw[:, b_idx].astype(np.int32) - 32768) * b_on
                sig //= 2  # bipolar spans twice the single-ended range
            if keep_mark:
                # The LAST word of every frame -- the hardware event line
                # (patient button, technician marks). It is not offset-binary:
                # EDFbrowser applies its high-byte fixup to the channel words
                # only, so this one is reinterpreted as int16 unchanged.
                ev = raw[:, n_ch_file : n_ch_file + 1].astype(np.int32)
                sig = np.concatenate([sig, np.where(ev > 32767, ev - 65536, ev)], 1)
            n_out = sig.shape[1]
            sig = sig.astype("<i2").reshape(nrec, sfreq * REC_SECS, n_out)
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


def write_sidecar(edf_path, blk, keep, e21, patient, dc_cal, montages, applied):
    """The sidecar information EDF itself has nowhere to put."""
    dob = patient.get("dob")
    meta = {
        "block": {
            "start": blk["start"][:14],
            "duration_s": blk["duration"],
            "sfreq": blk["sfreq"],
            "n_channels_in_file": blk["n_channels"],
        },
        "reference": e21.get("system_reference"),
        "device": e21.get("device"),
        "patient": {
            "sex": patient.get("sex"),
            "dob": dob.isoformat() if dob else None,
            "age_at_recording": patient.get("age"),
        },
        "channels": [
            {
                "label": blk["ch_names"][i],
                "e21_index": blk["e21_index"][i],
                "unit": signal_spec(
                    blk["ch_names"][i], blk["e21_index"][i], dc_cal
                )[1],
            }
            for i in keep
        ],
        "montages": [
            {"name": m["name"], "channels": [c["label"] for c in m["channels"]]}
            for m in montages
            if len({c["label"] for c in m["channels"]}) > 1
        ],
        "montage_applied": applied["name"] if applied else None,
    }
    with open(os.path.splitext(edf_path)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


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
        help="drop the per-frame event word instead of writing it as an "
             "Events/Markers signal",
    )
    ap.add_argument(
        "--montage",
        help="write a .PTN montage's bipolar traces instead of referential "
             "channels; NAME (e.g. EMU1) or 'auto' to follow the .LOG",
    )
    ap.add_argument(
        "--no-sidecar", action="store_true", help="skip the per-EDF .json metadata"
    )
    args = ap.parse_args()

    blocks = nk.read_blocks(args.input)
    n_file_ch = blocks[0]["n_channels"]

    side = nkmeta.discover(args.input)
    dc_cal = nkmeta.read_11d(side["11d"]) if "11d" in side else {}
    patient_info = nkmeta.read_pnt(side["pnt"]) if "pnt" in side else {}
    e21 = nkmeta.read_21e_sections(side["21e"]) if "21e" in side else {}
    montages = (
        nkmeta.read_ptn_dir(side["ptn"], nk.read_21e(side["21e"]), e21["reference"])
        if "ptn" in side and "21e" in side
        else []
    )
    by_name = {m["name"]: m for m in montages}

    if args.all_channels:
        keep = list(range(n_file_ch))
    else:
        keep = select_channels(blocks[0], dc_cal, nk.read_21e(side["21e"]))
    if not keep:
        raise SystemExit("no channels selected -- try --all-channels")

    # EDF+ patient field: sex and birth date only, never name or record number.
    if args.patient == "X X X X" and patient_info.get("dob"):
        d = patient_info["dob"]
        mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
               "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"][d.month - 1]
        args.patient = f"X {patient_info.get('sex') or 'X'} " \
                       f"{d.day:02d}-{mon}-{d.year} X"

    log_events = []
    log_path = args.log or (None if args.no_log else default_log_path(args.input))
    if log_path:
        log_events = nk.read_log(log_path)
        print(f"{os.path.basename(log_path)}: {len(log_events)} log events")
    elif not args.no_log:
        print("no .LOG found next to the .EEG -- converting without log annotations")
    if args.annotations:
        log_events += read_annotation_csv(args.annotations)
    if "sld" in side:
        marks = nkmeta.read_sld(side["sld"])
        if marks:
            print(f"{os.path.basename(side['sld'])}: {len(marks)} clinician bookmarks")
        log_events += marks

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
        print(f"sidecars: {', '.join(sorted(k for k in side if k != 'eeg')) or 'none'}")
        if e21.get("system_reference"):
            print(f"reference: {e21['system_reference']}  device: {e21.get('device')}")
        if patient_info.get("dob"):
            print(f"patient: sex {patient_info.get('sex')}  born {patient_info['dob']}"
                  f"  age at recording {patient_info.get('age')}")
        real = [m for m in montages if len({c["label"] for c in m["channels"]}) > 1]
        if real:
            print(f"montages ({len(real)}): "
                  + ", ".join(f"{m['name']}[{len(m['channels'])}]" for m in real))
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

        mont = None
        if args.montage:
            want = args.montage
            if want.lower() == "auto":
                want = nkmeta.montage_at(log_events, block_start, list(by_name))
                if not want:
                    raise SystemExit(
                        f"block {i}: the .LOG names no montage at {block_start}; "
                        "pass --montage NAME instead"
                    )
            mont = by_name.get(want)
            if mont is None:
                raise SystemExit(
                    f"no montage {want!r} -- available: {', '.join(sorted(by_name))}"
                )

        nrec, nch, start = convert_block(
            args.input, b, out, keep, args.patient, recording,
            args.ascii_labels, events=events, keep_mark=not args.no_mark_channel,
            dc_cal=dc_cal, montage=mont,
        )
        if not args.no_sidecar:
            write_sidecar(out, b, keep, e21, patient_info, dc_cal, montages, mont)
        size = os.path.getsize(out)
        print(
            f"[{i:2d}/{len(blocks)-1}] {os.path.basename(out)}  "
            f"{nch} ch  {nrec} s  {len(events)} annot  {size/1e6:.1f} MB"
            + (f"  montage {mont['name']}" if mont else ""),
            flush=True,
        )


if __name__ == "__main__":
    main()
