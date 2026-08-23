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

try:
    import edfio
except ImportError:
    edfio = None


def write_vwr(path, width=2):
    order, rate = 2, 4
    order_offset = vwr.HEADER_SIZE
    labcod_offset = order_offset + vwr.ORDER_SIZE
    notes_offset = labcod_offset + 2 * vwr.LABCOD_SIZE
    data_offset = notes_offset + vwr.NOTE_COUNT * vwr.NOTE_SIZE

    header = bytearray(vwr.HEADER_SIZE)
    header[:16] = b"Micromed VWR\x1a".ljust(16, b"\0")
    header[128:134] = bytes((2, 1, 124, 3, 4, 5))
    struct.pack_into("<I", header, 138, data_offset)
    struct.pack_into("<H", header, 142, order)
    struct.pack_into("<H", header, 146, rate)
    struct.pack_into("<H", header, 148, width)
    header[175] = 4
    for index, (name, offset, size) in enumerate((
        (b"ORDER", order_offset, vwr.ORDER_SIZE),
        (b"LABCOD", labcod_offset, 2 * vwr.LABCOD_SIZE),
        (b"NOTE", notes_offset, vwr.NOTE_COUNT * vwr.NOTE_SIZE),
    )):
        at = vwr.SEGMENT_FIRST + index * vwr.SEGMENT_SIZE
        header[at:at + 8] = name.ljust(8, b"\0")
        struct.pack_into("<II", header, at + 8, offset, size)

    order_table = struct.pack("<256H", 1, 0, *([0] * 254))
    labcod = bytearray(2 * vwr.LABCOD_SIZE)
    for index, (label, ground, lground, pmin, pmax) in enumerate((
        (b"A", b"REF", 100, 0, 256),
        (b"B", b"REF", 50, 0, 256),
    )):
        at = index * vwr.LABCOD_SIZE
        struct.pack_into("<BB", labcod, at, 1, 0)
        labcod[at + 2:at + 8] = label.ljust(6, b"\0")
        labcod[at + 8:at + 14] = ground.ljust(6, b"\0")
        struct.pack_into("<iiiii", labcod, at + 14, 0, 255, lground, pmin, pmax)
        struct.pack_into("<h", labcod, at + 34, 0)
    notes = bytearray(vwr.NOTE_COUNT * vwr.NOTE_SIZE)
    struct.pack_into("<I", notes, 0, 2)
    notes[4:9] = b"onset"

    values = np.array([[51, 101], [52, 102], [53, 103], [54, 104]], dtype=f"<u{width}")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(order_table)
        fh.write(labcod)
        fh.write(notes)
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
            self.assertEqual([(note.frame, note.description) for note in header.notes], [(2, "onset")])
            frames = next(vwr.iter_frames(header, header.n_samples))
            np.testing.assert_array_equal(frames, values)
            np.testing.assert_allclose(vwr.calibrated(frames, header.channels), [[1, 1], [2, 2], [3, 3], [4, 4]])

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
            header_size = 256 * (header.order + 2)
            samples = np.frombuffer(raw, dtype="<i2", count=header.order * header.frequency, offset=header_size)
            self.assertEqual(samples.size, 8)
            self.assertTrue(np.all(samples >= -32768))
            metadata = json.loads(output.with_suffix(".json").read_text())
            self.assertEqual(metadata["sampling_rate_hz"], 4)
            self.assertEqual(metadata["notes"][0]["onset_s"], 0.5)
            self.assertNotIn("patient_name", metadata)
            if edfio:
                edf = edfio.read_edf(output)
                self.assertEqual(edf.labels, ("B-REF", "A-REF"))
                np.testing.assert_allclose(edf.signals[0].data, [1, 2, 3, 4], atol=1.1)
                np.testing.assert_allclose(edf.signals[1].data, [1, 2, 3, 4], atol=1.1)
                self.assertEqual(len(edf.annotations), 1)


if __name__ == "__main__":
    unittest.main()
