import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import vwr
import vwr2edf

sys.path.insert(0, str(HERE.parent))
import edfcommon

try:
    import edfio
except ImportError:
    edfio = None

# LABCOD 0 is the recording ground, as in a real file: a montage input of 0
# means "this channel's own ground".
LABCOD = (
    (b"G2", b"", 0, 0, 256),
    (b"A", b"REF", 100, 0, 256),
    (b"B", b"REF", 50, 0, 256),
)
MONTAGES = (
    ("Bipolar", ((2, 1), (0, 2))),   # A-B, then B against its own ground
    ("Ground", ((0, 1),)),
)


def _montage_block(name, traces):
    block = bytearray(vwr.MONTAGE_SIZE)
    struct.pack_into("<4H", block, 0, len(traces), 0, 10, 1)
    block[vwr.MONTAGE_NAME:vwr.MONTAGE_NAME + len(name)] = name.encode()
    for index, (reference, active) in enumerate(traces):
        struct.pack_into("<2H", block, vwr.MONTAGE_INPUTS + index * 4, reference, active)
    return bytes(block)


def write_vwr(path, width=2):
    order, rate = 2, 4
    sizes = [
        ("ORDER", vwr.ORDER_SIZE),
        ("MONTAGE", len(MONTAGES) * vwr.MONTAGE_SIZE),
        ("LABCOD", len(LABCOD) * vwr.LABCOD_SIZE),
        ("NOTE", vwr.NOTE_COUNT * vwr.NOTE_SIZE),
        ("HISTORY", vwr.HISTORY_TIMES + vwr.MONTAGE_SIZE),
        ("TRIGGER", 8 * vwr.TRIGGER_SIZE),
        ("TRONCA", 2 * vwr.TRONCA_SIZE),
        ("FLAGS", vwr.FLAGS_SIZE),
        ("EVENT A", vwr.EVENT_NAME_SIZE + vwr.EVENT_COUNT * 8),
    ]
    offsets, at = {}, vwr.HEADER_SIZE
    for name, size in sizes:
        offsets[name] = at
        at += size
    data_offset = at

    header = bytearray(vwr.HEADER_SIZE)
    header[:16] = b"Micromed VWR\x1a".ljust(16, b"\0")
    header[128:134] = bytes((2, 1, 124, 3, 4, 5))
    struct.pack_into("<I", header, 138, data_offset)
    struct.pack_into("<H", header, 142, order)
    struct.pack_into("<H", header, 146, rate)
    struct.pack_into("<H", header, 148, width)
    header[175] = 4
    for index, (name, size) in enumerate(sizes):
        slot = vwr.SEGMENT_FIRST + index * vwr.SEGMENT_SIZE
        header[slot:slot + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<II", header, slot + 8, offsets[name], size)

    order_table = struct.pack("<256H", 2, 1, *([0] * 254))
    montage = b"".join(_montage_block(name, traces) for name, traces in MONTAGES)
    labcod = bytearray(len(LABCOD) * vwr.LABCOD_SIZE)
    for index, (label, ground, lground, pmin, pmax) in enumerate(LABCOD):
        slot = index * vwr.LABCOD_SIZE
        struct.pack_into("<BB", labcod, slot, 1, 0)
        labcod[slot + 2:slot + 8] = label.ljust(6, b"\0")
        labcod[slot + 8:slot + 14] = ground.ljust(6, b"\0")
        struct.pack_into("<iiiii", labcod, slot + 14, 0, 255, lground, pmin, pmax)
        struct.pack_into("<h", labcod, slot + 34, 0)
    notes = bytearray(vwr.NOTE_COUNT * vwr.NOTE_SIZE)
    notes[4:9] = b"start"  # frame 0, which the reader used to drop
    struct.pack_into("<I", notes, vwr.NOTE_SIZE, 2)
    notes[vwr.NOTE_SIZE + 4:vwr.NOTE_SIZE + 9] = b"onset"
    # HISTORY names the montage the recording was made with.
    history = b"\xff" * vwr.HISTORY_TIMES + _montage_block(*MONTAGES[1])
    trigger = struct.pack("<IH", 1, 7) + b"\xff\xff\xff\xff\x00\x00" * 7
    tronca = struct.pack("<4I", 10, 0, 20, 2)
    flags = struct.pack("<2I", 0, 2)
    event_a = b"Seizure".ljust(vwr.EVENT_NAME_SIZE, b"\0")
    event_a += struct.pack(f"<{vwr.EVENT_COUNT}I", 1, *([0] * (vwr.EVENT_COUNT - 1)))
    event_a += struct.pack(f"<{vwr.EVENT_COUNT}I", 3, *([0] * (vwr.EVENT_COUNT - 1)))

    values = np.array([[51, 101], [52, 102], [53, 103], [54, 104]], dtype=f"<u{width}")
    blocks = {"ORDER": order_table, "MONTAGE": montage, "LABCOD": bytes(labcod),
              "NOTE": bytes(notes), "HISTORY": history, "TRIGGER": trigger,
              "TRONCA": tronca, "FLAGS": flags, "EVENT A": event_a}
    with open(path, "wb") as fh:
        fh.write(header)
        for name, size in sizes:
            assert len(blocks[name]) == size, name
            fh.write(blocks[name])
        fh.write(values.tobytes())
    return values


class VwrReaderTest(unittest.TestCase):
    def test_header_order_calibration_and_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.vwr"
            values = write_vwr(path)
            header = vwr.read_header(path)

            self.assertEqual(header.start.isoformat(), "2024-01-02T03:04:05")
            self.assertEqual([channel.name for channel in header.channels], ["B-REF", "A-REF"])
            self.assertEqual([(n.frame, n.description) for n in header.notes],
                             [(0, "start"), (2, "onset")])
            frames = next(vwr.iter_frames(header, header.n_samples))
            np.testing.assert_array_equal(frames, values)
            np.testing.assert_allclose(vwr.calibrated(frames, header.channels),
                                       [[1, 1], [2, 2], [3, 3], [4, 4]])

    def test_all_libvwr_sample_widths(self):
        with tempfile.TemporaryDirectory() as directory:
            for width in (1, 2, 4):
                path = Path(directory) / f"record-{width}.vwr"
                values = write_vwr(path, width)
                header = vwr.read_header(path)
                np.testing.assert_array_equal(next(vwr.iter_frames(header, 4)), values)

    def test_rejects_incomplete_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.vwr"
            write_vwr(path)
            with open(path, "ab") as fh:
                fh.write(b"\0")
            with self.assertRaisesRegex(ValueError, "trailing"):
                vwr.read_header(path)


class VwrMontageTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "record.vwr"
        write_vwr(self.path)
        self.header = vwr.read_header(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def test_montages_are_found_by_name_not_segment_position(self):
        # MONTAGE sits between ORDER and LABCOD in the synthetic file.
        self.assertEqual([m.name for m in self.header.montages], ["Bipolar", "Ground"])
        self.assertEqual(self.header.montage("Bipolar").notch, 1)

    def test_input_zero_resolves_to_the_channels_own_ground(self):
        traces = self.header.montage("Bipolar").traces
        self.assertEqual([t.label for t in traces], ["A-B", "B-REF"])
        self.assertEqual((traces[1].active, traces[1].reference, traces[1].reference_index),
                         ("B", "REF", 0))

    def test_history_names_the_recorded_montage(self):
        self.assertEqual(self.header.recorded_montage, "Ground")

    def test_montage_pairs_map_onto_order_positions(self):
        # ORDER is (LABCOD 2, LABCOD 1), so B is position 0 and A is position 1.
        pairs = vwr2edf.montage_pairs(self.header, self.header.montage("Bipolar"))
        self.assertEqual(pairs, [("A-B", 1, 0), ("B-REF", 0, None)])

    def test_montage_output_labels_and_values(self):
        output = Path(self.directory.name) / "montage.edf"
        montage = self.header.montage("Bipolar")
        self.assertEqual(vwr2edf.convert(self.header, output, "X X X X", True, montage), 2)
        meta = json.loads(output.with_suffix(".json").read_text())
        self.assertEqual([c["edf_label"] for c in meta["channels"]], ["A-B", "B-REF"])
        self.assertEqual(meta["montage_applied"], "Bipolar")
        self.assertEqual([c["derived"] for c in meta["channels"]], [True, False])
        if edfio:
            edf = edfio.read_edf(output)
            self.assertEqual(edf.labels, ("A-B", "B-REF"))
            # Both channels calibrate to 1..4, so their difference is flat zero.
            np.testing.assert_allclose(edf.signals[0].data, [0, 0, 0, 0], atol=0.11)
            np.testing.assert_allclose(edf.signals[1].data, [1, 2, 3, 4], atol=0.01)

    def test_unknown_montage_is_rejected(self):
        self.assertIsNone(self.header.montage("nope"))


class VwrMarkerTest(unittest.TestCase):
    def test_every_marker_area_is_read_and_sorted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.vwr"
            write_vwr(path)
            header = vwr.read_header(path)
            self.assertEqual(
                [(m.frame, m.label, m.source, m.end_frame) for m in header.markers],
                [(0, "Flag 1", "FLAGS", 2),
                 (0, "start", "NOTE", None),
                 (1, "Seizure", "EVENT A", 3),
                 (1, "Trigger 7", "TRIGGER", None),
                 (2, "onset", "NOTE", None)],
            )
            self.assertEqual([(p.frame, p.original_frame) for p in header.parts],
                             [(0, 10), (2, 20)])

    def test_intervals_become_annotations_with_a_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "record.vwr"
            write_vwr(path)
            header = vwr.read_header(path)
            events = vwr2edf._events(header)
            self.assertEqual(events[0], (0.0, "Flag 1", 0.5))
            self.assertEqual(events[2], (0.25, "Seizure", 0.5))
            self.assertIn(b"+0\x150.5\x14Flag 1", edfcommon._tal(0.0, "Flag 1", 0.5))


class VwrEdfTest(unittest.TestCase):
    def test_edf_and_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "record.vwr"
            output = Path(directory) / "record.edf"
            write_vwr(source)
            header = vwr.read_header(source)
            vwr2edf.convert(header, output, "X X X X", sidecar=True)

            raw = output.read_bytes()
            self.assertEqual(raw[0:8].rstrip(), b"0")
            self.assertIn(b"onset", raw)
            self.assertIn(b"Trigger 7", raw)
            header_size = 256 * (header.order + 2)
            samples = np.frombuffer(raw, dtype="<i2",
                                    count=header.order * header.frequency, offset=header_size)
            self.assertEqual(samples.size, 8)
            self.assertTrue(np.all(samples >= -32768))

            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["schema"], edfcommon.SCHEMA)
            self.assertEqual(metadata["clip"]["sfreq_hz"], 4)
            self.assertEqual(metadata["source"]["format"], "micromed-vwr")
            self.assertIsNone(metadata["montage_applied"])
            self.assertEqual(metadata["events"][0]["label"], "Flag 1")
            self.assertEqual(metadata["events"][0]["duration_s"], 0.5)
            self.assertEqual(len(metadata["segments"]), 2)
            self.assertNotIn("patient_name", metadata)
            self.assertEqual(json.dumps(metadata["patient"]),
                             json.dumps({"sex": None, "dob": None, "age_at_recording": None}))
            if edfio:
                edf = edfio.read_edf(output)
                self.assertEqual(edf.labels, ("B-REF", "A-REF"))
                np.testing.assert_allclose(edf.signals[0].data, [1, 2, 3, 4], atol=1.1)
                np.testing.assert_allclose(edf.signals[1].data, [1, 2, 3, 4], atol=1.1)
                self.assertEqual(len(edf.annotations), 5)

    def test_last_record_is_padded_at_physical_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "record.vwr"
            output = Path(directory) / "record.edf"
            write_vwr(source)
            header = vwr.read_header(source)
            # 4 samples at 4 Hz is exactly one record; ask for a longer record
            # so the tail is padded.
            object.__setattr__(header, "frequency", 8)
            vwr2edf.convert(header, output, "X X X X", sidecar=False)
            if edfio:
                edf = edfio.read_edf(output)
                np.testing.assert_allclose(edf.signals[0].data[4:], 0, atol=0.11)


if __name__ == "__main__":
    unittest.main()
