import struct
from pathlib import Path
import tempfile
import unittest

from server import FTParser, crc8, find_device_port


class DeviceDiscoveryTests(unittest.TestCase):
    def test_replug_with_a_different_usb_serial(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            pattern = 'usb-GigaDevice_GD32-CDC_ACM_*-if00'
            with self.assertRaises(ConnectionError):
                find_device_port(pattern, directory)
            old = directory / 'usb-GigaDevice_GD32-CDC_ACM_D57969778787-if00'
            old.touch()
            self.assertEqual(find_device_port(pattern, directory), str(old))
            old.unlink()
            new = directory / 'usb-GigaDevice_GD32-CDC_ACM_D25D69774287-if00'
            new.touch()
            self.assertEqual(find_device_port(pattern, directory), str(new))
            old.touch()
            with self.assertRaisesRegex(ConnectionError, 'Multiple matching'):
                find_device_port(pattern, directory)


class FTProtocolTests(unittest.TestCase):
    def test_vendor_examples(self):
        # Published commands and USB response from official PX6D manual §5.2.2.
        for hexdata in [
            'AA557F03049F', 'AA557F0400E8', 'AA557F0501FA',
            'AA557F0701D0',
            'AA557F03310C0BBD5C7638BDFC35A23C6EE8B7BAE69C243936C89FB943',
        ]:
            frame = bytes.fromhex(hexdata)
            self.assertEqual(crc8(frame[:-1]), frame[-1])
        parser = FTParser()
        values = parser.feed(bytes.fromhex(
            'AA557F03310C0BBD5C7638BDFC35A23C6EE8B7BAE69C243936C89FB943'))
        self.assertEqual(len(values), 1)
        self.assertAlmostEqual(values[0][0], -.03394717, places=7)
        self.assertAlmostEqual(values[0][5], -.00030476, places=7)

    def test_chunk_boundaries_and_corrupt_data(self):
        body = FTParser.HEAD + struct.pack('<6f', -1.5, 2., -3., .01, -.02, .03)
        good = body + bytes([crc8(body)])
        bad = bytearray(good)
        bad[9] ^= 1
        parser = FTParser()
        stream = b'noise\xaa' + good + bad + b'other response' + good + good
        result = []
        for i in range(0, len(stream), 7):
            result.extend(parser.feed(stream[i:i + 7]))
        self.assertEqual(len(result), 3)
        self.assertEqual(parser.invalid, 1)
        self.assertAlmostEqual(result[-1][0], -1.5)
        self.assertAlmostEqual(result[-1][-1], .03, places=6)

    def test_nonfinite_values_rejected(self):
        body = FTParser.HEAD + struct.pack('<6f', float('nan'), 0, 0, 0, 0, 0)
        parser = FTParser()
        self.assertEqual(parser.feed(body + bytes([crc8(body)])), [])
        self.assertEqual(parser.invalid, 1)

    def test_start_ack_is_not_a_bad_data_frame(self):
        body = FTParser.HEAD + struct.pack('<6f', 0, 0, 0, 0, 0, 0)
        frame = body + bytes([crc8(body)])
        parser = FTParser(expect_start_ack=True)
        output = []
        stream = FTParser.START_ACK + frame + frame
        for byte in stream:
            output.extend(parser.feed(bytes([byte])))
        self.assertEqual(output, [[0.] * 6, [0.] * 6])
        self.assertEqual(parser.invalid, 0)


if __name__ == '__main__':
    unittest.main()
