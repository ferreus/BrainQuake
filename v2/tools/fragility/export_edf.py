"""Export a peri-ictal window from an EDF into the CSV pair run_frag.R reads.

Neural fragility (Li et al. 2021) is computed by the EZFragility R package, so
the crossing point between this repository and that analysis is a pair of plain
files per seizure:

    {label}.csv      contacts x samples, common-average-referenced microvolts
    {label}_ch.txt   one contact name per line, in the same row order

See README.md for why the format is what it is.
"""

import argparse
import os
import re
import sys

import mne
import numpy as np

# An SEEG contact label is a shaft letter, optionally primed for the
# contralateral shaft, then the contact number -- A1, D6, X'12, G'10. Anything
# else in the file is an auxiliary trace (REF1, DC01, EKG1, UNUSED248, MARK, a
# bare E) and must not reach the analysis: it would be ranked as if it were a
# contact, and it would land in the common-average reference below, where a DC
# input stored in mV or an unitless mark word sits orders of magnitude above a
# microvolt contact and swamps every one of them.
#
# Kept as its own copy rather than imported from the server package: this
# script needs only mne + numpy, while app.services.edf_common pulls in
# pydantic-settings and SQLAlchemy. The canonical definition is
# v2/server/app/services/edf_common.py:find_non_seeg_channels -- change both.
_SEEG_CONTACT_RE = re.compile(r"^[A-Za-z]'?\d+$")


def select_contacts(ch_names):
    """The SEEG contacts among `ch_names`, in the recording's own order.

    Order is preserved rather than sorted because it is the only thing tying
    the CSV's rows to the channel-name file; whatever order comes out of here
    must be the order both files are written in.
    """
    return [n for n in ch_names if _SEEG_CONTACT_RE.match(str(n).strip())]


def resolve_onset(raw, pattern):
    """Seconds of the one annotation whose description matches `pattern`.

    Requires exactly one match. A seizure marked twice, or a pattern that also
    catches the clinical-onset note a second later, is a mistake worth stopping
    for -- the whole analysis is anchored on this number.
    """
    rx = re.compile(pattern)
    hits = [
        (float(onset), str(desc))
        for onset, desc in zip(raw.annotations.onset, raw.annotations.description, strict=True)
        if rx.search(str(desc))
    ]
    if not hits:
        raise SystemExit(
            f"no annotation matches {pattern!r}. Run with --list-annotations to see what is in the file."
        )
    if len(hits) > 1:
        listed = ", ".join(f"{t:.3f}s {d!r}" for t, d in hits)
        raise SystemExit(f"{len(hits)} annotations match {pattern!r}, need exactly one: {listed}")
    return hits[0][0]


def export_window(edf_path, onset_sec, out_dir, label, pre=20.0, post=10.0, car=True):
    """Write {label}.csv and {label}_ch.txt for the window around `onset_sec`.

    The window is inclusive of both endpoints, so it holds
    (pre + post) * fs + 1 samples -- the R side rebuilds its time axis as
    (seq_len(ncol) - 1) / fs - pre, which puts sample 0 at -pre and the onset
    at exactly t = 0.
    """
    # Manifests are hand-edited and their paths are relative, so a typo here is
    # the likeliest failure of the whole tool. Say which path, not a traceback.
    if not os.path.exists(edf_path):
        raise SystemExit(f"{label}: no such recording: {edf_path}")

    raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose="error")
    fs = raw.info["sfreq"]
    duration = float(raw.times[-1])

    start, end = onset_sec - pre, onset_sec + post
    # Clamping would silently shift the onset away from t=0 and leave the R
    # side's time axis lying about which window is ictal.
    if start < 0 or end > duration:
        raise SystemExit(
            f"window {start:.3f}-{end:.3f}s does not fit inside the {duration:.3f}s recording "
            f"(onset {onset_sec:.3f}s, pre {pre}s, post {post}s)"
        )

    contacts = select_contacts(raw.ch_names)
    if not contacts:
        raise SystemExit(
            f"no channel in {os.path.basename(edf_path)} is named like an SEEG contact "
            f"(shaft letter + number, e.g. A1 or X'12); first few are {raw.ch_names[:6]}"
        )
    dropped = [n for n in raw.ch_names if n not in set(contacts)]
    picks = [raw.ch_names.index(n) for n in contacts]

    i0 = int(round(start * fs))
    data = raw.get_data(picks=picks, start=i0, stop=i0 + int(round((pre + post) * fs)) + 1)
    data = data * 1e6  # mne returns volts; the R side and the reference data are microvolts
    if car:
        data = data - data.mean(axis=0)

    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, f"{label}.csv")
    ch_path = os.path.join(out_dir, f"{label}_ch.txt")
    # 4 decimals is well below the amplifier's 0.098 uV/LSB step, so this is
    # lossless in practice and keeps the file a third the size of full repr.
    np.savetxt(csv_path, data, fmt="%.4f", delimiter=",")
    with open(ch_path, "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(contacts) + "\n")

    print(
        f"{label}: {data.shape[0]} contacts x {data.shape[1]} samples "
        f"({start:.3f}-{end:.3f}s, onset at t=0), dropped {len(dropped)} auxiliary channel(s)"
        + (f": {dropped}" if dropped else "")
    )
    return csv_path, ch_path


def read_manifest(path):
    """label,edf_path,onset per line; onset is seconds or @<annotation regex>.

    '#' comments and blank lines are ignored. Relative EDF paths resolve
    against the manifest's own directory, so a manifest can sit next to the
    recordings and stay portable.
    """
    base = os.path.dirname(os.path.abspath(path))
    rows = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 3:
                raise SystemExit(f"{path}:{lineno}: expected 'label,edf_path,onset', got {line!r}")
            label, edf_path, onset = parts
            if not os.path.isabs(edf_path):
                edf_path = os.path.join(base, edf_path)
            rows.append((label, edf_path, onset))
    return rows


def _onset_for(edf_path, onset_spec):
    if onset_spec.startswith("@"):
        if not os.path.exists(edf_path):
            raise SystemExit(f"no such recording: {edf_path}")
        raw = mne.io.read_raw_edf(edf_path, preload=False, stim_channel=None, verbose="error")
        return resolve_onset(raw, onset_spec[1:])
    try:
        return float(onset_spec)
    except ValueError:
        raise SystemExit(
            f"onset {onset_spec!r} is neither a number of seconds nor '@<annotation regex>'"
        ) from None


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Export peri-ictal windows from EDF recordings for run_frag.R.",
        epilog="Onsets may be given as seconds, or as @REGEX to take them from the EDF+ annotations.",
    )
    p.add_argument("edf", nargs="?", help="input .edf (omit when using --manifest)")
    p.add_argument("-o", "--out", default=".", help="output directory (default: current)")
    p.add_argument("-l", "--label", help="output basename, e.g. SZ1P")
    p.add_argument("--onset", help="seizure onset in seconds, or @REGEX to match an annotation")
    p.add_argument("--manifest", help="CSV of 'label,edf_path,onset' rows to export in one run")
    p.add_argument("--pre", type=float, default=20.0, help="seconds before onset (default: 20)")
    p.add_argument("--post", type=float, default=10.0, help="seconds after onset (default: 10)")
    p.add_argument(
        "--no-car",
        action="store_true",
        help="skip the common-average reference (it is applied by default)",
    )
    p.add_argument(
        "--list-annotations",
        action="store_true",
        help="print the EDF's annotations with their onsets and exit",
    )
    args = p.parse_args(argv)

    if args.list_annotations:
        if not args.edf:
            p.error("--list-annotations needs an edf")
        raw = mne.io.read_raw_edf(args.edf, preload=False, stim_channel=None, verbose="error")
        for onset, desc in zip(raw.annotations.onset, raw.annotations.description, strict=True):
            print(f"{float(onset):10.3f}  {desc}")
        return 0

    if args.manifest:
        if args.edf or args.onset or args.label:
            p.error("--manifest is exclusive with edf/--onset/--label")
        jobs = read_manifest(args.manifest)
    else:
        if not (args.edf and args.onset and args.label):
            p.error("need edf, --onset and --label (or --manifest)")
        jobs = [(args.label, args.edf, args.onset)]

    for label, edf_path, onset_spec in jobs:
        export_window(
            edf_path,
            _onset_for(edf_path, onset_spec),
            args.out,
            label,
            pre=args.pre,
            post=args.post,
            car=not args.no_car,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
