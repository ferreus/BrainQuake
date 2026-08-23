"""Every converter's sidecar must be the same shape. See SIDECAR.md."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
for sub in ("", "nk2edf", "nicolet2edf", "vwr2edf"):
    sys.path.insert(0, str(HERE / sub))

import edfcommon
import nicolet2edf
import nk2edf
import vwr2edf
from test_vwr2edf import write_vwr

import vwr

TOP = ["schema", "source", "clip", "device", "reference", "patient",
       "channels", "montages", "montage_applied", "segments", "events"]


def nk_sidecar(path):
    blk = {"start": "20240206195547", "duration": 10.0, "sfreq": 500,
           "n_channels": 2, "e21_index": [0, 1], "ch_names": ["Fp1", "Fp2"]}
    signals = [("Fp1", "uV", -3200.0, 3199.902), ("Fp2", "uV", -3200.0, 3199.902),
               ("Events/Markers", "", -1, 1)]
    montages = [{"name": "EMU1", "channels": [
        {"a": 1, "b": 2, "active": "Fp1", "reference": "Fp2",
         "label": "Fp1-Fp2", "row": 0},
        {"a": 2, "b": 1, "active": "Fp2", "reference": "Fp1",
         "label": "Fp2-Fp1", "row": 1}]}]
    nk2edf.write_sidecar(
        path, "CA6476I6.EEG", blk, ["Fp1", "Fp2"], [(0, None), (1, None)], signals,
        {"system_reference": "A1", "device": "EEG-1200A"},
        {"sex": "F", "dob": dt.date(2020, 5, 1), "age": 3},
        montages, None, [(1.0, "REC START EMU1 EEG")],
    )


def nicolet_sidecar(path):
    def channel(label, ref):
        return {"label": label, "active": label, "reference": ref, "sfreq": 512,
                "resolution": 0.15, "low_cut": 0.1, "high_cut": 100.0, "notch": 50.0}

    header = {
        "path": "/data/Bella.e",
        "channels": [channel("Fp1", "REF"), channel("Fp2", "REF"), channel("Rate", "")],
        "montage": {"name": "Bipolar", "channels": [
            {"label": "Fp1-Fp2", "active": "Fp1", "reference": "Fp2"}]},
        "segments": [{"start": dt.datetime(2022, 9, 21, 3, 50, 11), "duration": 10.0}],
        "events": [{"when": dt.datetime(2022, 9, 21, 3, 50, 13), "duration": 2.0,
                    "type": "Annotation", "guid": "{...}", "channel": None,
                    "label": "onset"}],
    }
    pairs = [("Fp1", 0, None), ("Fp2", 1, None)]
    clip = (0, header["segments"][0]["start"], 0.0, 10.0)
    nicolet2edf.write_sidecar(path, header, pairs, clip, True, False)


def vwr_sidecar(path):
    source = path.with_suffix(".vwr")
    write_vwr(source)
    header = vwr.read_header(source)
    vwr2edf.convert(header, path, "X X X X", sidecar=True, montage=header.montage("Bipolar"))


class SidecarSchemaTest(unittest.TestCase):
    def sidecars(self, directory):
        out = {}
        for name, build in (("nk2edf", nk_sidecar), ("nicolet2edf", nicolet_sidecar),
                            ("vwr2edf", vwr_sidecar)):
            path = Path(directory) / f"{name}.edf"
            build(path)
            out[name] = json.loads(path.with_suffix(".json").read_text())
        return out

    def test_all_three_share_the_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, meta in self.sidecars(directory).items():
                with self.subTest(tool=name):
                    self.assertEqual(meta["schema"], edfcommon.SCHEMA)
                    self.assertEqual(list(meta)[:len(TOP)], TOP)
                    self.assertEqual(sorted(meta["source"]), ["file", "format"])
                    for key, template in (("clip", edfcommon.CLIP),
                                          ("patient", edfcommon.PATIENT)):
                        self.assertEqual(list(meta[key])[:len(template)], list(template))
                    # segments is [] for a format with no sub-segments (nk2edf).
                    self.assertTrue(meta["channels"] and meta["events"], name)
                    for section, template in (("channels", edfcommon.CHANNEL),
                                              ("segments", edfcommon.SEGMENT),
                                              ("events", edfcommon.EVENT)):
                        for row in meta[section]:
                            self.assertEqual(list(row)[:len(template)], list(template))
                    for montage in meta["montages"]:
                        self.assertEqual(sorted(montage), ["channels", "name"])
                        for trace in montage["channels"]:
                            self.assertEqual(list(trace)[:len(edfcommon.TRACE)],
                                             list(edfcommon.TRACE))

    def test_types_that_used_to_differ_per_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, meta in self.sidecars(directory).items():
                with self.subTest(tool=name):
                    # was a raw YYYYMMDDhhmmss string in nk2edf
                    dt.datetime.fromisoformat(meta["clip"]["start"])
                    # was a single dict in nicolet2edf
                    self.assertIsInstance(meta["montages"], list)
                    # was a bool in nicolet2edf, a name in nk2edf
                    self.assertIsInstance(meta["montage_applied"], (str, type(None)))
                    # was absent in nk2edf
                    self.assertIsInstance(meta["events"], list)

    def test_channels_describe_the_edf_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            meta = self.sidecars(directory)["vwr2edf"]
            self.assertEqual(meta["montage_applied"], "Bipolar")
            self.assertEqual([c["edf_label"] for c in meta["channels"]], ["A-B", "B-REF"])


if __name__ == "__main__":
    unittest.main()
