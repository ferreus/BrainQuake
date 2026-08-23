#!/usr/bin/env python3
"""Open an EDF in MNE's interactive browser with clinical review settings.

Usage:
    python show_edf.py FILE SENS TC HICUT PAGE CLOCK [SHAFTS]

    FILE    EDF file
    SENS    sensitivity in uV/mm, as on the Nihon Kohden Sens dropdown; a trace row
            is ROW_MM tall, so one row spans SENS * ROW_MM uV
    TC      time constant in seconds, applied as NK does it: a causal single-pole
            RC high-pass (0.1 s = 1.6 Hz, -6 dB/oct). "1.6Hz" gives the cut
            directly, "off" disables it
    HICUT   high cut in Hz, "off" to disable
    PAGE    seconds of signal per screen
    CLOCK   wall clock position HH:MM:SS (wraps past midnight)
    SHAFTS  optional comma-separated shafts to show, e.g. "G'" or "G',L'";
            default is every channel

Options:
    --ref [CH]  subtract a reference channel and label traces NAME-CH, the way the
                NK review shows G'1-REF1; bare --ref means REF1
    --row-mm MM trace row spacing, fitted to the NK display (default 12)
    --positive-up   plot positive deflections upward; the default is the
                    clinical convention NK uses, negative up

Example:
    python show_edf.py somefile.edf 75 0.1 300 10 07:24:35 "G'" --ref REF1
"""
import argparse
import math
import re
import sys

# Full match: some exports carry a second, junk bank whose labels got "-1" suffixes
# to stay unique. G'1 is a contact, G'1-1 is not.
CONTACT_RE = re.compile(r"([A-Za-z]+'?)\s*(\d+)$")


ROW_MM = 12.0  # trace spacing on the NK review, fitted against its screenshots


def fmt_clock(seconds):
    s = seconds % 86400
    return f"{int(s // 3600):02d}:{int(s // 60) % 60:02d}:{s % 60:06.3f}"


def parse_tc(text):
    """Time constant in seconds; 'Hz' suffix gives the equivalent low cut instead."""
    text = text.strip().lower()
    if text in ("off", "none", "0"):
        return None
    if text.endswith("hz"):
        return 1.0 / (2 * math.pi * float(text[:-2]))
    return float(text.rstrip("sec"))


def parse_hicut(text):
    text = text.strip().lower()
    if text in ("off", "none", "0"):
        return None
    return float(text.rstrip("hz"))


def parse_clock(text):
    parts = text.strip().split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"bad clock time {text!r}, expected HH:MM:SS")
    h, m = int(parts[0]), int(parts[1])
    s = float(parts[2]) if len(parts) == 3 else 0.0
    return h * 3600 + m * 60 + s


def pick_shafts(ch_names, shafts):
    """Channels belonging to the requested shafts, ordered shaft then contact."""
    wanted = [s.strip() for s in shafts.split(",") if s.strip()]
    picked = []
    for name in ch_names:
        m = CONTACT_RE.fullmatch(name.replace("POL ", "").replace("EEG ", "").strip())
        if not m:
            continue
        shaft, number = m.group(1), int(m.group(2))
        for rank, w in enumerate(wanted):
            if shaft.lower() == w.lower():
                picked.append((rank, number, name))
                break
    return [name for _, _, name in sorted(picked)]


def main():
    p = argparse.ArgumentParser(
        description="Show an EDF in MNE's browser at given sensitivity, filters and clock time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="example: show_edf.py rec.edf 75 0.1 300 10 07:24:35 \"G'\"",
    )
    p.add_argument("edf")
    p.add_argument("sens", type=float, help="uV/mm, as on the NK Sens dropdown")
    p.add_argument("tc", help="time constant in seconds, or e.g. 1.6Hz / off")
    p.add_argument("hicut", help="high cut in Hz, or off")
    p.add_argument("page", type=float, help="seconds per page")
    p.add_argument("clock", help="HH:MM:SS")
    p.add_argument("shafts", nargs="?", help="shafts to show, e.g. \"G',L'\"")
    p.add_argument("--ref", nargs="?", const="REF1", metavar="CH",
                   help="subtract this channel and label traces NAME-CH (default REF1)")
    p.add_argument("--row-mm", type=float, default=ROW_MM, metavar="MM",
                   help=f"trace row spacing in mm (default {ROW_MM:g})")
    p.add_argument("--positive-up", action="store_true",
                   help="plot positive up; default is negative up, as NK does")
    args = p.parse_args()

    import matplotlib
    import mne

    if matplotlib.get_backend().lower() == "agg":
        matplotlib.use("TkAgg")  # mne's browser needs an interactive backend

    tc = parse_tc(args.tc)
    hicut = parse_hicut(args.hicut)
    target = parse_clock(args.clock)

    raw = mne.io.read_raw_edf(args.edf, preload=False, verbose="error")

    if args.shafts:
        names = pick_shafts(raw.ch_names, args.shafts)
        if not names:
            sys.exit(f"no channels match {args.shafts!r}; file has: {', '.join(raw.ch_names)}")
        raw.pick(names + ([args.ref] if args.ref else []))
        raw.reorder_channels(names + ([args.ref] if args.ref else []))

    need_gb = len(raw.ch_names) * raw.n_times * 8 / 2**30
    if need_gb > 1:
        print(f"note: preloading {need_gb:.1f} GB", file=sys.stderr)
    raw.load_data(verbose="error")

    if args.ref:
        if args.ref not in raw.ch_names:
            sys.exit(f"reference channel {args.ref!r} not in file")
        anodes = [c for c in raw.ch_names if c != args.ref]
        raw = mne.set_bipolar_reference(
            raw, anode=anodes, cathode=[args.ref] * len(anodes),
            ch_name=[f"{c}-{args.ref}" for c in anodes], verbose="error")

    if tc:
        # NK's TC filter is a causal one-pole RC high-pass. mne's own display
        # high-pass is a zero-phase 4-pole butter, which eats far more slow
        # activity at the same corner and makes the traces look flat.
        from scipy.signal import lfilter
        a = tc / (tc + 1.0 / raw.info["sfreq"])
        raw.apply_function(lambda x: lfilter([a, -a], [1.0, -a], x), verbose="error")

    if not args.positive_up:
        import numpy as np
        raw.apply_function(np.negative, verbose="error")  # clinical EEG: negative up

    nyquist = raw.info["sfreq"] / 2
    if hicut is not None and hicut >= nyquist:
        print(f"warning: high cut {hicut:g} Hz is at/above Nyquist "
              f"({nyquist:g} Hz) -- disabled", file=sys.stderr)
        hicut = None

    start_dt = raw.info["meas_date"]
    duration = raw.n_times / raw.info["sfreq"]
    if start_dt is None:
        sys.exit("EDF has no start time; cannot seek to a clock time")
    file_start = start_dt.hour * 3600 + start_dt.minute * 60 + start_dt.second
    offset = target - file_start
    if offset < 0:
        offset += 86400  # recording crossed midnight
    if offset > duration:
        sys.exit(f"{args.clock} is outside the file "
                 f"({fmt_clock(file_start)}..{fmt_clock(file_start + duration)}, "
                 f"{duration:.1f} s)")

    row_uv = args.sens * args.row_mm
    scaling = row_uv / 2 * 1e-6  # mne gives each trace 2*scaling of row height
    scalings = {t: scaling for t in ("eeg", "seeg", "ecog", "dbs", "misc",
                                     "emg", "ecg", "eog", "bio")}

    print(f"{len(raw.ch_names)} channels, {raw.info['sfreq']:g} Hz, "
          f"start {start_dt.strftime('%H:%M:%S')}, {duration:.1f} s")
    print(f"sensitivity {args.sens:g} uV/mm ({row_uv:g} uV per row), "
          f"TC {'off' if tc is None else f'{tc:g} s = {1 / (2 * math.pi * tc):.2f} Hz'}, "
          f"high cut {'off' if hicut is None else f'{hicut:g} Hz'}, "
          f"page {args.page:g} s at +{offset:.3f} s, "
          f"{'positive' if args.positive_up else 'negative'} up")

    raw.plot(
        start=offset,
        duration=args.page,
        n_channels=min(len(raw.ch_names), 32),  # scroll rather than squash a full montage
        scalings=scalings,
        highpass=None,  # already applied, NK-style
        lowpass=hicut,
        clipping=None,
        time_format="clock",
        title=f"{args.edf} @ {args.clock}",
        block=True,
    )


if __name__ == "__main__":
    main()
