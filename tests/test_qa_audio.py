import struct
import unittest

import io
import math

import numpy as np
import soundfile

from app.qa.audio import filename_from_disposition, gsm_to_wav, identify, looks_like_raw_gsm


def gsm_tone(seconds=1.0):
    """Raw GSM 6.10 bytes like xCALLY's .gsm recordings (8 kHz, 33-byte frames)."""
    tone = (0.5 * np.sin(2 * math.pi * 440 * np.arange(int(8000 * seconds)) / 8000)).astype("float32")
    out = io.BytesIO()
    soundfile.write(out, tone, 8000, format="RAW", subtype="GSM610")
    return out.getvalue()


def wav(tag):
    fmt = struct.pack("<HHIIHH", tag, 1, 8000, 16000, 2, 16)
    return b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"LIST" + struct.pack("<I", 4) + b"INFO" + b"fmt " + struct.pack("<I", len(fmt)) + fmt


class IdentifyTests(unittest.TestCase):
    def test_wav_codecs(self):
        self.assertEqual(identify(wav(1), "application/octet-stream"),
                         {"media_type": "audio/wav", "container": "WAV", "codec": "PCM", "playable": True})
        self.assertEqual(identify(wav(0x31), None)["codec"], "GSM 6.10")
        self.assertFalse(identify(wav(0x31), None)["playable"])
        self.assertIsNone(identify(wav(7), None)["playable"])

    def test_other_containers(self):
        self.assertEqual(identify(b"ID3\x04rest", None)["media_type"], "audio/mpeg")
        self.assertEqual(identify(b"\xff\xfb\x90", None)["container"], "MP3")
        self.assertEqual(identify(b"OggS\x00", None)["media_type"], "audio/ogg")

    def test_non_audio_is_rejected(self):
        for head, declared in ((b' {"url": "x"}', "application/json"), (b"<html>", "text/html"), (b"", None)):
            self.assertIsNone(identify(head, declared)["media_type"])
        self.assertEqual(identify(b"\x01\x02", "audio/x-gsm")["media_type"], "audio/x-gsm")



class GsmTests(unittest.TestCase):
    def test_raw_gsm_is_detected_by_frames_or_filename(self):
        data = gsm_tone()
        self.assertEqual(data[0] >> 4, 0xD)
        self.assertTrue(looks_like_raw_gsm(data[:4096]))
        info = identify(data[:4096], "application/octet-stream")
        self.assertEqual((info["container"], info["media_type"], info["convert"]), ("GSM", "audio/wav", True))
        self.assertTrue(identify(b"\x01\x02\x03", "application/octet-stream", "x.gsm")["convert"])
        self.assertFalse(looks_like_raw_gsm(b"ID3" + bytes(100)))
        self.assertNotIn("convert", identify(wav(1), None, "x.gsm"))

    def test_filename_from_disposition(self):
        self.assertEqual(filename_from_disposition('attachment; filename="1e68.gsm"'), "1e68.gsm")
        self.assertEqual(filename_from_disposition(None), "")

    def test_gsm_converts_to_playable_wav_with_same_length(self):
        wav_bytes = gsm_to_wav(gsm_tone(2.0) + b"\xd0\x00")  # a trailing partial frame is dropped
        self.assertEqual(identify(wav_bytes[:64], None)["codec"], "PCM")
        samples, rate = soundfile.read(io.BytesIO(wav_bytes), dtype="int16")
        self.assertEqual((rate, len(samples)), (8000, 16000))
        self.assertGreater(int(np.abs(samples).max()), 1000)
        with self.assertRaises(ValueError):
            gsm_to_wav(b"\xd0")


if __name__ == "__main__":
    unittest.main()
