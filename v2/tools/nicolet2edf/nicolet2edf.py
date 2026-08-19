"""Convert Nicolet/Nervus .e files to EDF+C, one file per recorded segment.

The only other way to open these is nrveeg_export.exe, a 2008 debug build whose
source was lost and which needs proprietary Nicolet COM DLLs. See README.md.

Segments are what the recorder actually stored, and they are not contiguous --
Bella.e holds 2750 s of data spanning 4.5 h of wall clock. Writing one EDF per
segment keeps every start time true; --concat glues them into the single
timeline the vendor exporter produced, with the joins marked as annotations.

Samples are negated: the stored int16 is inverted relative to uV, confirmed by
comparing converted output against the Nicolet viewer. --no-invert writes them
as stored. Magnitudes pass through untouched either way, so the EDF digital
range IS the stored range and the conversion stays exactly lossless.

Output is referential as stored, against REF. --montage writes the file's own
display montage as bipolar traces instead.

Usage:
  python nicolet2edf.py INPUT.e OUTDIR [--segments 0,2-3] [--all-channels]
                                       [--concat] [--no-invert] [--montage]
                                       [--list] [--patient "X X X X"]
                                       [--no-sidecar]
"""
import argparse
import datetime as dt
import json
import os

import nicolet
import numpy as np

REC_SECS = 1  # EDF data record duration
MIN_ANNOT_BYTES = 120  # floor for the EDF Annotations signal, per record

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

# A derived trend (Rate/IBI/Bursts/Suppr) is a number the Nicolet software
# computed, not a measurement; its resolution carries no documented dimension.
EEG_UNIT = "uV"
DERIVED_UNIT = ""


# --- EDF writing -----------------------------------------------------------
# _fld/_num/_tal/plan_annotations are copies of the same helpers in
# ../nk2edf/nk2edf.py -- fix a bug in one and fix it in the other. build_header
# differs: signals here carry their own samples-per-record, because a Nicolet
# file mixes 512 Hz EEG with 1 Hz trends in one recording.

def _fld(text, width):
    """Left-justified fixed-width ASCII EDF header field."""
    b = str(text).encode("ascii", "replace")[:width]
    return b + b" " * (width - len(b))


def _num(value, width):
    """EDF numeric field: as many significant digits as fit, no exponent.

    Nicolet resolutions are not round numbers, so the physical range lands on
    something like -5481.90848 and every character of the field counts -- the
    gain a reader recovers is only as good as what fits in these 8 bytes.
    """
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    for dec in range(width, -1, -1):
        if len(s) <= width:
            return _fld(s, width)
        s = f"{value:.{dec}f}"
    return _fld(s[:width], width)


def build_header(signals, n_records, start, patient, recording, annot_bytes):
    """`signals` is a list of (label, unit, phys_min, phys_max, samples_per_record)
    for the data signals; the EDF Annotations signal is appended here."""
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
    h += b"".join(_fld("", 80) for _ in range(ns))          # transducer
    h += b"".join(_fld(s[1], 8) for s in signals) + _fld("", 8)   # units
    h += b"".join(_num(s[2], 8) for s in signals) + _fld("-1", 8)  # phys min
    h += b"".join(_num(s[3], 8) for s in signals) + _fld("1", 8)   # phys max
    h += b"".join(_fld(-32768, 8) for _ in range(ns))
    h += b"".join(_fld(32767, 8) for _ in range(ns))
    h += b"".join(_fld("", 80) for _ in range(ns))          # prefiltering
    h += b"".join(_fld(s[4], 8) for s in signals)
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


# --- clips and events ------------------------------------------------------

def clips(header, sel, concat):
    """What to write, as (name_index, start, offset into the stream, duration).

    The stored stream is the segments back to back, so a clip is just a second
    range into it -- concatenated output is the whole range, and that is the
    only difference between the two modes.
    """
    starts, at = [], 0.0
    for seg in header["segments"]:
        starts.append(at)
        at += seg["duration"]
    if concat:
        return [(None, header["segments"][0]["start"], 0.0, at)]
    return [(i, header["segments"][i]["start"], starts[i], header["segments"][i]["duration"])
            for i in sel]


def stream_seconds(header, when):
    """Wall clock -> seconds into the stored stream, or None if it falls in the
    time between segments, which the recording simply does not contain."""
    at = 0.0
    for seg in header["segments"]:
        if seg["start"] <= when < seg["start"] + dt.timedelta(seconds=seg["duration"]):
            return at + (when - seg["start"]).total_seconds()
        at += seg["duration"]
    return None


def event_text(ev):
    """What the Nicolet viewer shows: a bare annotation is its own text, any
    other kind is prefixed with its type ("Prune - #1")."""
    if not ev["label"]:
        return ev["type"]
    if ev["type"] == "Annotation":
        return ev["label"]
    return f"{ev['type']} - {ev['label']}"


def clip_events(header, offset_s, duration):
    """Events landing inside one clip, as (onset, text).

    An unrecognised event GUID with no label carries nothing a reader could
    use, so it stays in the sidecar and out of the EDF -- the viewer does not
    show those either.
    """
    out = []
    for ev in header["events"]:
        if not ev["label"] and ev["type"].startswith("{"):
            continue
        at = stream_seconds(header, ev["when"])
        if at is None:
            continue
        onset = at - offset_s
        if 0 <= onset < duration:
            out.append((onset, event_text(ev)))
    return sorted(out)


def join_annotations(header, duration):
    """One marker per segment join, so a concatenated file still says where the
    recorder stopped and how much wall clock was skipped."""
    segments = header["segments"]
    out, at = [], 0.0
    for i, seg in enumerate(segments):
        if i and at < duration:
            prev = segments[i - 1]
            end = prev["start"] + dt.timedelta(seconds=prev["duration"])
            out.append((at, f"Segment {i} start {seg['start']:%Y-%m-%d %H:%M:%S} "
                            f"(gap {(seg['start'] - end).total_seconds():.0f} s)"))
        at += seg["duration"]
    return out


# --- conversion ------------------------------------------------------------

def select_channels(channels, all_channels):
    """Raw EEG by default. The derived trends run at a different rate and are
    software output, not signal."""
    keep = [i for i, c in enumerate(channels) if all_channels or c["reference"]]
    return keep or list(range(len(channels)))


def signal_spec(channels, label, a, b):
    """(label, unit, phys_min, phys_max, samples_per_record).

    The physical range is the digital range times the resolution, so digital
    values pass through untouched and referential output is lossless.
    """
    ca = channels[a]
    res, sfreq = ca["resolution"], ca["sfreq"]
    if b is not None:
        cb = channels[b]
        if (cb["resolution"], cb["sfreq"]) != (res, sfreq):
            raise SystemExit(f"{label}: {ca['label']} and {cb['label']} differ in "
                             "resolution or sampling rate, cannot be subtracted")
        res *= 2  # a bipolar trace spans twice the single-ended range
    spr = sfreq * REC_SECS
    if spr != int(spr):
        raise SystemExit(f"{label}: {sfreq} Hz is not a whole number of "
                         f"samples per {REC_SECS} s record")
    unit = EEG_UNIT if ca["reference"] else DERIVED_UNIT
    return (label, unit, -32768 * res, 32767 * res, int(spr))


def referential_pairs(channels, keep):
    """(label, a, b) per signal; referential output has no b."""
    names = unique_labels(channels, keep)
    return [(names[j], c, None) for j, c in enumerate(keep)]


def montage_pairs(header):
    """Map the file's own display montage onto channel positions.

    A derivation whose reference the recording does not carry -- EKG-Bipolar
    names none -- is the active channel alone, which is what the viewer shows.
    """
    pos = {c["label"]: i for i, c in enumerate(header["channels"])}
    out = []
    for m in header["montage"]["channels"]:
        a = pos.get(m["active"])
        if a is None:
            continue
        out.append((m["label"], a, pos.get(m["reference"])))
    return out


def unique_labels(channels, keep):
    """EDF readers key on labels, so they have to be distinct."""
    names, seen = [], {}
    for i in keep:
        n = channels[i]["label"].strip() or f"CH{i}"
        if n in seen:
            seen[n] += 1
            n = f"{n}-{seen[n]}"
        else:
            seen[n] = 0
        names.append(n)
    return names


def convert_clip(streams, header, pairs, out_path, start, offset_s, duration,
                 patient, recording, events):
    channels = header["channels"]
    signals = [signal_spec(channels, lab, a, b) for lab, a, b in pairs]
    n_records = int(round(duration / REC_SECS))

    views = []
    for j, (lab, a, b) in enumerate(pairs):
        spr = signals[j][4]
        lo = int(round(offset_s * channels[a]["sfreq"]))
        want = n_records * spr

        def take(ch, lo=lo, want=want, lab=lab):
            block = streams[ch][lo:lo + want]
            if len(block) != want:
                raise EOFError(f"{lab}: {len(block)} samples for {want} needed")
            return block

        block = take(a)
        if b is not None:
            # Both sides share REF, so A-B cancels it. Halved to stay in int16;
            # signal_spec doubled the physical range to match.
            block = (block.astype(np.int32) - take(b)) // 2
        views.append(np.ascontiguousarray(block, dtype="<i2").reshape(n_records, spr))

    per_record, annot_bytes = plan_annotations(events, n_records)
    header_bytes = build_header(signals, n_records, start, patient, recording, annot_bytes)

    with open(out_path, "wb") as fout:
        fout.write(header_bytes)
        for r in range(n_records):
            fout.write(b"".join(v[r].tobytes() for v in views))  # per-signal blocks
            tals = _tal(r * REC_SECS) + b"".join(per_record.get(r, ()))
            if len(tals) > annot_bytes:  # plan_annotations sized this
                raise AssertionError(f"record {r}: {len(tals)} > {annot_bytes} annotation bytes")
            fout.write(tals + b"\x00" * (annot_bytes - len(tals)))
    return n_records, len(pairs)


def write_sidecar(edf_path, header, keep, clip, invert, applied):
    """The metadata EDF itself has nowhere to put."""
    montage = header["montage"]
    meta = {
        "source": os.path.basename(header["path"]),
        "clip": {"start": clip[1].isoformat(), "offset_s": clip[2], "duration_s": clip[3],
                 "segment": clip[0]},
        "polarity_inverted": invert,
        "montage": montage and {"name": montage["name"], "channels": montage["channels"]},
        "montage_applied": bool(applied),
        "segments": [{"start": s["start"].isoformat(), "duration_s": s["duration"]}
                     for s in header["segments"]],
        "channels": [
            {
                "label": header["channels"][c]["label"],
                "active_sensor": header["channels"][c]["active"],
                "reference": header["channels"][c]["reference"] or None,
                "sfreq": header["channels"][c]["sfreq"],
                "resolution_uv_per_lsb": header["channels"][c]["resolution"],
                "low_cut": header["channels"][c]["low_cut"],
                "high_cut": header["channels"][c]["high_cut"],
                "notch": header["channels"][c]["notch"],
                "derived": not header["channels"][c]["reference"],
            }
            for c in keep
        ],
        "events": [
            {
                "when": e["when"].isoformat(),
                "stream_s": stream_seconds(header, e["when"]),
                "duration_s": e["duration"],
                "type": e["type"],
                "guid": e["guid"],
                "channel": e["channel"],
                "label": e["label"],
            }
            for e in header["events"]
        ],
    }
    with open(os.path.splitext(edf_path)[0] + ".json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)


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
        raise SystemExit(f"segment index out of range: {bad}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("outdir", nargs="?")
    ap.add_argument("--segments", help="e.g. 0,2-3 (default: all)")
    ap.add_argument("--all-channels", action="store_true",
                    help="keep the derived trend channels too")
    ap.add_argument("--concat", action="store_true",
                    help="one EDF of every segment back to back, as the vendor "
                         "exporter produced; joins are marked as annotations")
    ap.add_argument("--no-invert", action="store_true",
                    help="write samples as stored instead of negating them -- see "
                         "README, the stored samples are inverted relative to uV")
    ap.add_argument("--montage", action="store_true",
                    help="write the file's own display montage as bipolar traces "
                         "instead of the referential channels")
    ap.add_argument("--list", action="store_true", help="list segments and channels and exit")
    ap.add_argument("--patient", default="X X X X")
    ap.add_argument("--no-sidecar", action="store_true", help="skip the per-EDF .json metadata")
    args = ap.parse_args()

    header = nicolet.read_header(args.input)
    channels = header["channels"]
    keep = select_channels(channels, args.all_channels)
    total = sum(s["duration"] for s in header["segments"])

    if args.list:
        print(f"{args.input}  {header['size']/1e6:.1f} MB")
        print(f"{len(channels)} channels in file, exporting {len(keep)}")
        print(f"{len(header['events'])} events, {len(header['segments'])} segments, "
              f"{total:g} s of data")
        print()
        print(f"{'#':>3} {'start':>19} {'dur(s)':>8} {'MB out':>8}")
        for i, s in enumerate(header["segments"]):
            mb = sum(round(s["duration"] * channels[c]["sfreq"]) for c in keep) * 2 / 1e6
            when = f"{s['start']:%Y-%m-%d %H:%M:%S}"
            print(f"{i:3d} {when:>19} {s['duration']:8.1f} {mb:8.1f}")
        print()
        for i, c in enumerate(channels):
            mark = " " if i in keep else "-"
            print(f"{mark}{i:3d} {c['label']:<8} {c['sfreq']:>6g} Hz  "
                  f"{c['resolution']:.6f} uV/LSB  ref={c['reference'] or '-'}")
        if header["montage"]:
            m = header["montage"]
            print(f"montage {m['name']!r}, {len(m['channels'])} traces: "
                  + ", ".join(c["label"] for c in m["channels"]))
        print()
        for ev in header["events"]:
            at = stream_seconds(header, ev["when"])
            print(f"  {ev['when']:%H:%M:%S}  {'' if at is None else f'{at:8.1f}s'}"
                  f"  {event_text(ev)}")
        return

    if not args.outdir:
        raise SystemExit("outdir required (or use --list)")
    os.makedirs(args.outdir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.input))[0]
    sel = parse_sel(args.segments, len(header["segments"]))
    if args.concat and args.segments:
        raise SystemExit("--concat writes every segment; drop --segments")

    if args.montage:
        if not header["montage"]:
            raise SystemExit(f"no montage stored in {args.input}")
        pairs = montage_pairs(header)
        print(f"montage {header['montage']['name']!r}: {len(pairs)} traces", flush=True)
    else:
        pairs = referential_pairs(channels, keep)

    need = sorted({a for _, a, _ in pairs} | {b for _, _, b in pairs if b is not None})
    invert = not args.no_invert
    print(f"reading {len(need)} channels", flush=True)
    with open(args.input, "rb") as fh:
        streams = {}
        for c in need:
            v = np.frombuffer(nicolet.read_channel(fh, header, c), dtype="<i2")
            # Corrected before any montage subtracts, so the arithmetic runs on
            # true uV. -32768 has no positive int16 counterpart: clamp, do not wrap.
            streams[c] = -np.clip(v, -32767, None) if invert else v

    todo = clips(header, sel, args.concat)
    for done, clip in enumerate(todo, 1):
        i, start, offset_s, duration = clip
        name = stem if i is None else f"{stem}_{i:02d}_{start:%Y%m%d%H%M%S}"
        out = os.path.join(args.outdir, name + ".edf")
        # EDF+ recording field is strictly
        # "Startdate dd-MMM-yyyy <admin code> <investigator> <equipment>"
        recording = (f"Startdate {start.day:02d}-{MONTHS[start.month - 1]}-{start.year} "
                     f"{stem} X Nicolet/Nervus")
        events = clip_events(header, offset_s, duration)
        if args.concat:
            events = sorted(events + join_annotations(header, duration))

        nrec, nch = convert_clip(streams, header, pairs, out, start, offset_s, duration,
                                 args.patient, recording, events)
        if not args.no_sidecar:
            write_sidecar(out, header, keep, clip, invert, args.montage)
        print(f"[{done}/{len(todo)}] {os.path.basename(out)}  "
              f"{nch} ch  {nrec} s  {len(events)} annot  "
              f"{os.path.getsize(out)/1e6:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
