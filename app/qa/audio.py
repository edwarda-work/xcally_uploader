"""Identify what xCALLY actually sent for a recording download.

The API spec documents the download as JSON, and Asterisk-based systems often
store calls as GSM-encoded .wav files that browsers cannot play. Reading the
first bytes tells us the real container and codec, so the page can explain a
failure instead of showing a dead player.
"""

from __future__ import annotations

import io
import re
import struct

# WAVE format tags (fmt chunk "audio format" field).
WAV_CODECS = {
    0x0001: "PCM", 0x0003: "IEEE float", 0x0006: "A-law", 0x0007: "mu-law",
    0x0002: "MS ADPCM", 0x0011: "IMA ADPCM", 0x0031: "GSM 6.10", 0xFFFE: "extensible",
}
# Raw GSM 6.10 (Asterisk ".gsm"): headerless 33-byte frames of 20 ms at 8 kHz,
# each starting with the 0xD signature nibble. xCALLY stores calls this way.
GSM_FRAME_BYTES = 33
GSM_SAMPLE_RATE = 8000
MAX_GSM_BYTES = 50 * 1024 * 1024  # about 8.5 hours of audio

# Codecs no mainstream browser decodes inside WAV.
UNPLAYABLE_WAV = {"MS ADPCM", "IMA ADPCM", "GSM 6.10"}


def _wav_codec(head: bytes) -> str | None:
    offset = 12
    while offset + 8 <= len(head):
        chunk_id, size = head[offset:offset + 4], struct.unpack("<I", head[offset + 4:offset + 8])[0]
        if chunk_id == b"fmt " and offset + 10 <= len(head):
            tag = struct.unpack("<H", head[offset + 8:offset + 10])[0]
            return WAV_CODECS.get(tag, f"format 0x{tag:04X}")
        offset += 8 + size + (size & 1)
    return None


def filename_from_disposition(disposition: str | None) -> str:
    match = re.search(r'filename="?([^";]+)"?', disposition or "")
    return match.group(1).strip() if match else ""


def looks_like_raw_gsm(head: bytes) -> bool:
    frames = [head[i] for i in range(0, len(head) - GSM_FRAME_BYTES + 1, GSM_FRAME_BYTES)][:30]
    return bool(frames) and all(byte >> 4 == 0xD for byte in frames)


def identify(head: bytes, declared_type: str | None, filename: str = "") -> dict:
    """Return media_type, container, codec, playable (True/False/None = unknown) and convert.

    Raw GSM cannot play in a browser, so it is marked for conversion to WAV.
    """
    declared = (declared_type or "").split(";")[0].strip().lower()
    if head and (filename.lower().endswith(".gsm") or looks_like_raw_gsm(head)) and head[:4] != b"RIFF":
        return {"media_type": "audio/wav", "container": "GSM", "codec": "GSM 6.10",
                "playable": True, "convert": True}
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        codec = _wav_codec(head)
        return {"media_type": "audio/wav", "container": "WAV", "codec": codec,
                "playable": False if codec in UNPLAYABLE_WAV else (True if codec in {"PCM", "IEEE float"} else None)}
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return {"media_type": "audio/mpeg", "container": "MP3", "codec": "MP3", "playable": True}
    if head[:4] == b"OggS":
        return {"media_type": "audio/ogg", "container": "Ogg", "codec": None, "playable": True}
    if head[:6] == b"#!AMR\n":
        return {"media_type": "audio/amr", "container": "AMR", "codec": "AMR", "playable": False}
    stripped = head.lstrip()[:1]
    if stripped in (b"{", b"[") or declared == "application/json":
        return {"media_type": None, "container": "JSON", "codec": None, "playable": False}
    if stripped == b"<" or declared == "text/html":
        return {"media_type": None, "container": "HTML", "codec": None, "playable": False}
    if not head:
        return {"media_type": None, "container": "empty", "codec": None, "playable": False}
    return {"media_type": declared or "application/octet-stream", "container": "unknown",
            "codec": None, "playable": None}


def gsm_to_wav(data: bytes) -> bytes:
    """Decode raw GSM 6.10 into a 16-bit PCM WAV file that every browser plays."""
    import soundfile  # bundles libsndfile on macOS and Windows

    usable = len(data) - len(data) % GSM_FRAME_BYTES
    if not usable:
        raise ValueError("The recording is empty.")
    samples, rate = soundfile.read(io.BytesIO(data[:usable]), format="RAW", subtype="GSM610",
                                   samplerate=GSM_SAMPLE_RATE, channels=1, dtype="int16")
    out = io.BytesIO()
    soundfile.write(out, samples, rate, format="WAV", subtype="PCM_16")
    return out.getvalue()
