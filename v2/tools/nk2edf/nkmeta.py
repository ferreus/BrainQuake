"""Readers for the sidecar files Nihon Kohden writes next to a .EEG.

Every reader degrades to None/{} when its file is missing or malformed -- a
missing sidecar must never fail a conversion.

Pattern_NNN.PTN (montage), verified against the Nihon Kohden viewer:
  0x0080  16s   montage name ("ETEST", "EMU1", ...)
  0x0410 + i*0x50   channel record:
    +0  u8   electrode A, index into .21E [ELECTRODE]
    +1  u8   electrode B, may instead be a [REFERENCE] code (0x29 = $0V)
    +2  u8   sensitivity (0x0f EEG, 0x05 EKG)
    +7  u8   visible: 1 shown, 0 hidden -- this is what sets the channel
             count. The byte at 0xD5 reads 19 in every pattern and is not it.
    +12 u16  display row, 0xFFFF when hidden
"""
import datetime as dt
import os
import re
import struct

PTN_NAME = 0x80
PTN_FIRST = 0x410
PTN_STRIDE = 0x50
PTN_SLOTS = 64  # the grid ends at 0x1810; past that the bytes are not records

# .PNT fixed slots; verified identical across CA06911B / CA6476I6 / DA064JXK.
PNT_SEX = (0x64A, 8)
PNT_DOB = (0x660, 10)
PNT_AGE = (0x672, 6)


def discover(eeg_path):
    """Every sibling sharing the .EEG's stem, keyed by lowercased extension."""
    base = os.path.dirname(os.path.abspath(eeg_path))
    stem = os.path.splitext(os.path.basename(eeg_path))[0].lower()
    found = {}
    try:
        entries = os.listdir(base)
    except OSError:
        return found
    for name in entries:
        root, ext = os.path.splitext(name)
        if root.lower() != stem or not ext:
            continue
        found.setdefault(ext[1:].lower(), os.path.join(base, name))
    return found


def _ini(path):
    """Nihon Kohden's sidecars are all INI-shaped: {section: {key: value}}."""
    out, section = {}, None
    try:
        fh = open(path, encoding="latin-1")
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if line.startswith("["):
                section = line.strip("[]").upper()
                out.setdefault(section, {})
            elif "=" in line and section is not None:
                key, val = line.split("=", 1)
                out[section].setdefault(key.strip(), val.strip())
    return out


def read_21e_sections(path):
    """Reference codes and the system reference the recording was made against."""
    ini = _ini(path)
    refs = {
        int(k): v.lstrip("$")
        for k, v in ini.get("REFERENCE", {}).items()
        if k.isdigit() and v
    }
    setup = ini.get("SYSTEM_SETUP", {})
    return {
        "reference": refs,
        "system_reference": setup.get("SystemReference"),
        "device": setup.get("DeviceName"),
    }


def read_ptn_dir(dirpath, labels, refs=None):
    """Montage definitions from a .PTN folder, in Pattern_NNN order."""
    refs = refs or {}

    def name_of(i):
        return labels.get(i) or refs.get(i) or f"#{i}"

    out = []
    try:
        files = sorted(f for f in os.listdir(dirpath) if f.lower().endswith(".ptn"))
    except OSError:
        return out
    for fn in files:
        try:
            d = open(os.path.join(dirpath, fn), "rb").read()
        except OSError:
            continue
        if len(d) < PTN_FIRST + PTN_SLOTS * PTN_STRIDE:
            continue
        name = d[PTN_NAME : PTN_NAME + 16].split(b"\0")[0].decode("latin-1").strip()
        chans = []
        for i in range(PTN_SLOTS):
            o = PTN_FIRST + i * PTN_STRIDE
            if d[o + 7] != 1:
                continue
            a, b = d[o], d[o + 1]
            chans.append(
                {"a": a, "b": b, "active": name_of(a), "reference": name_of(b),
                 "label": f"{name_of(a)}-{name_of(b)}",
                 "row": struct.unpack_from("<H", d, o + 12)[0]}
            )
        if name and chans:
            out.append({"name": name, "file": fn, "channels": chans})
    return out


def read_11d(path):
    """DC channel calibration from [ConvertDisplay]: {'DC01': {...}, ...}."""
    conv = _ini(path).get("CONVERTDISPLAY", {})
    out = {}
    for key, val in conv.items():
        if "_" not in key:
            continue
        ch, field = key.split("_", 1)
        out.setdefault(ch, {})[field.lower()] = val
    for ch, d in out.items():
        d["enable"] = d.get("enable", "").upper() == "TRUE"
        for f in ("coefficient", "offset"):
            try:
                d[f] = float(d.get(f, 0))
            except ValueError:
                d[f] = 0.0
    return out


def read_pnt(path):
    """Sex, birth date and age only. Name and medical record number are
    deliberately not returned so they cannot reach a converted file."""
    try:
        d = open(path, "rb").read()
    except OSError:
        return {}
    if len(d) < PNT_AGE[0] + PNT_AGE[1]:
        return {}

    def fld(slot):
        off, n = slot
        return d[off : off + n].decode("latin-1").strip("\x00 ").strip()

    sex = fld(PNT_SEX)
    dob = None
    try:
        dob = dt.datetime.strptime(fld(PNT_DOB), "%Y/%m/%d").date()
    except ValueError:
        pass
    return {"sex": sex[:1].upper() if sex else None, "dob": dob, "age": fld(PNT_AGE)}


# "20240206195547006347" -- YYYYMMDDhhmmss then microseconds.
_SLD_WHEN = re.compile(r"^(\d{14})(\d{6})")


def read_sld(path):
    """Clinician bookmarks as [{'when', 'text'}] -- same shape as nk.read_log."""
    ini = _ini(path)
    events = []
    for section, keys in ini.items():
        if not section.startswith("BOOKMARK"):
            continue
        for key, text in keys.items():
            if not key.startswith("EventName") or not text:
                continue
            n = key[len("EventName"):]
            m = _SLD_WHEN.match(keys.get(f"EventJumpInfo{n}", ""))
            if not m:
                continue
            try:
                when = dt.datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
            except ValueError:
                continue
            events.append(
                {"when": when + dt.timedelta(microseconds=int(m.group(2))),
                 "text": text}
            )
    return sorted(events, key=lambda e: e["when"])


def read_egf(path):
    """Clip metadata; 'videos' are (start, end, relative path) triples."""
    out, videos = {}, []
    try:
        fh = open(path, encoding="latin-1")
    except OSError:
        return out
    with fh:
        for line in fh:
            key, _, val = line.strip().partition("=")
            if not val:
                continue
            if key.startswith("mpeg"):
                parts = val.split(",")
                if len(parts) == 3:
                    videos.append(tuple(p.strip() for p in parts))
            elif key in ("IMAGEDIR", "TIME", "videoversion", "ClipAttribute"):
                out[key.lower()] = val.strip()
    if videos:
        out["videos"] = videos
    return out


def montage_at(log_events, when, names):
    """Name of the last montage the .LOG names at or before `when`.

    Entries read "REC START ETEST EEG" / "PAT EMU1 EEG", so the pattern in use
    at any moment is recoverable without opening the .PTN files.
    """
    best, best_when = None, None
    for e in log_events:
        if e["when"] > when:
            continue
        for n in names:
            if n and re.search(rf"\b{re.escape(n)}\b", e["text"]):
                if best_when is None or e["when"] >= best_when:
                    best, best_when = n, e["when"]
    return best
