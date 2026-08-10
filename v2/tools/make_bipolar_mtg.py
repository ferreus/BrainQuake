#!/usr/bin/env python3
"""Generate an EDFbrowser bipolar montage (.mtg) from an SEEG EDF.

Pairs consecutive contacts within each shaft (A1-A2, A2-A3, ...). Contacts are
identified by label convention -- shaft letter, optional prime, number -- so
auxiliary traces (DC*, REF*, MARK, UNUSED*) are left out automatically.

Usage:
    python make_bipolar_mtg.py INPUT.edf OUTPUT.mtg [--shafts G' L' A] [--ekg]
"""
import argparse
import re
import sys
from collections import OrderedDict

CONTACT_RE = re.compile(r"^([A-Za-z]'?)(\d+)$")

# Qt global colors EDFbrowser accepts (2..18); white/light grey omitted.
SHAFT_COLORS = [2, 15, 13, 14, 17, 16, 18, 4]

XML_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def esc(s):
    return "".join(XML_ESC.get(c, c) for c in s)


def read_edf_header(path):
    """Per-signal label, sample count, physical dimension and bit value."""
    with open(path, "rb") as f:
        head = f.read(256)
        if head[:8].strip() not in (b"0", b"\xffBIOSEMI"):
            print(f"warning: unexpected EDF version field {head[:8]!r}", file=sys.stderr)
        rec_dur = float(head[244:252])
        ns = int(head[252:256])

        def fields(width):
            return [f.read(width).decode("latin-1") for _ in range(ns)]

        labels = [x.rstrip() for x in fields(16)]
        fields(80)  # transducer
        dims = [x.strip() for x in fields(8)]
        pmin = [float(x) for x in fields(8)]
        pmax = [float(x) for x in fields(8)]
        dmin = [float(x) for x in fields(8)]
        dmax = [float(x) for x in fields(8)]
        fields(80)  # prefiltering
        nsamp = [int(x) for x in fields(8)]

    sigs = []
    for i in range(ns):
        span = dmax[i] - dmin[i]
        sigs.append({
            "index": i,
            "label": labels[i],
            "dim": dims[i],
            "nsamp": nsamp[i],
            "bitvalue": (pmax[i] - pmin[i]) / span if span else 0.0,
            "srate": nsamp[i] / rec_dur if rec_dur else 0.0,
        })
    return sigs


def group_shafts(sigs):
    """{shaft: [(number, signal), ...]} in contact order, primed shafts first."""
    shafts = {}
    for s in sigs:
        m = CONTACT_RE.match(s["label"])
        if m:
            shafts.setdefault(m.group(1), []).append((int(m.group(2)), s))
    for entries in shafts.values():
        entries.sort(key=lambda e: e[0])
    # primed shafts first (one hemisphere), alphabetical within each group
    order = sorted(shafts, key=lambda k: (not k.endswith("'"), k.upper()))
    return OrderedDict((k, shafts[k]) for k in order)


def compatible(a, b):
    """EDFbrowser refuses a composition whose signals differ in any of these."""
    return (a["nsamp"] == b["nsamp"]
            and a["dim"] == b["dim"]
            and abs(a["bitvalue"] - b["bitvalue"]) < 1e-12)


def composition(sig_a, sig_b, color, voltpercm):
    lines = [
        "  <signalcomposition>",
        "    <num_of_signals>2</num_of_signals>",
        f"    <voltpercm>{voltpercm:e}</voltpercm>",
        "    <screen_offset>0.000000e+00</screen_offset>",
        "    <screen_offset_unit>0.000000e+00</screen_offset_unit>",
        "    <polarity>1</polarity>",
        f"    <color>{color}</color>",
        "    <spike_filter_cnt>0</spike_filter_cnt>",
        "    <math_func_cnt_before>0</math_func_cnt_before>",
        "    <math_func_cnt_after>0</math_func_cnt_after>",
        "    <filter_cnt>0</filter_cnt>",
        "    <fidfilter_cnt>0</fidfilter_cnt>",
        "    <ravg_filter_cnt>0</ravg_filter_cnt>",
        "    <fir_filter_cnt>0</fir_filter_cnt>",
    ]
    for sig, factor in ((sig_a, 1.0), (sig_b, -1.0)):
        lines += [
            "    <signal>",
            f"      <label>{esc(sig['label'])}</label>",
            f"      <factor>{factor:e}</factor>",
            "    </signal>",
        ]
    lines.append("  </signalcomposition>")
    return lines


def build(sigs, shafts, voltpercm, pagetime, with_ekg):
    out = ['<?xml version="1.0"?>', "<EDFbrowser_montage>"]
    pairs = skipped = 0

    for n, (shaft, entries) in enumerate(shafts.items()):
        color = SHAFT_COLORS[n % len(SHAFT_COLORS)]
        for (num_a, sig_a), (num_b, sig_b) in zip(entries, entries[1:]):
            if num_b != num_a + 1:
                print(f"note: {shaft}: gap between contact {num_a} and {num_b}, "
                      f"pairing them anyway", file=sys.stderr)
            if not compatible(sig_a, sig_b):
                print(f"skip: {sig_a['label']}-{sig_b['label']} "
                      f"(sample rate / unit / gain mismatch)", file=sys.stderr)
                skipped += 1
                continue
            out += composition(sig_a, sig_b, color, voltpercm)
            pairs += 1

    if with_ekg:
        by_label = {s["label"]: s for s in sigs}
        a, b = by_label.get("EKG1"), by_label.get("EKG2")
        if a and b and compatible(a, b):
            out += composition(a, b, 7, voltpercm * 10)  # red, coarser scale
            pairs += 1

    out += [f"  <pagetime>{pagetime}</pagetime>", "</EDFbrowser_montage>", ""]
    return "\n".join(out), pairs, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edf")
    ap.add_argument("mtg")
    ap.add_argument("--shafts", nargs="+", metavar="S",
                    help="only these shafts, e.g. --shafts A I G'")
    ap.add_argument("--voltpercm", type=float, default=100.0,
                    help="amplitude scale in the signal's physical unit (default 100)")
    ap.add_argument("--pagetime", type=float, default=10.0,
                    help="page width in seconds (default 10)")
    ap.add_argument("--ekg", action="store_true", help="append an EKG1-EKG2 trace")
    args = ap.parse_args()

    sigs = read_edf_header(args.edf)
    shafts = group_shafts(sigs)
    if not shafts:
        sys.exit("no channel matches the SEEG contact convention (letter + number)")

    if args.shafts:
        wanted = set(args.shafts)
        missing = wanted - set(shafts)
        if missing:
            sys.exit(f"no such shaft(s): {sorted(missing)}; "
                     f"file has {sorted(shafts)}")
        shafts = OrderedDict((k, v) for k, v in shafts.items() if k in wanted)

    xml, pairs, skipped = build(sigs, shafts, args.voltpercm,
                                int(args.pagetime * 1e7), args.ekg)
    with open(args.mtg, "w", encoding="latin-1", newline="\n") as f:
        f.write(xml)

    print(f"{args.mtg}: {pairs} traces from {len(shafts)} shafts "
          f"({', '.join(shafts)})" + (f", {skipped} skipped" if skipped else ""))


if __name__ == "__main__":
    main()
