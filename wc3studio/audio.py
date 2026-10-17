"""Audio: inventory and extraction of every sound in the game.

The archives hold 6193 audio files: music, ambience, campaign dialogue, unit voice
lines, ability sounds and interface clicks. This module groups them for humans,
measures how long they play and copies them out untouched.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

__all__ = ["AudioFile", "CATEGORIES", "categorise", "duration_seconds", "inventory",
           "extract", "summary"]

AUDIO_EXTENSIONS = (".wav", ".mp3")

# Order matters: the first matching prefix wins, "other" catches the rest.
CATEGORIES = {
    "music": ("sound\\music",),
    "ambient": ("sound\\ambient",),
    "dialogue": ("sound\\dialogue",),
    "abilities": ("abilities\\", "sound\\abilities"),
    "units": ("units\\", "buildings\\", "sound\\units"),
    "ui": ("sound\\interface", "sound\\ui", "ui\\sounds"),
    "other": (),
}

_MP3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_MP3_RATES = (44100, 48000, 32000, 0)


@dataclass
class AudioFile:
    internal: str
    size: int
    category: str
    duration: float | None = None

    @property
    def name(self) -> str:
        return self.internal.rsplit("\\", 1)[-1]


def categorise(internal: str) -> str:
    lower = internal.replace("/", "\\").lower()
    for category, prefixes in CATEGORIES.items():
        for prefix in prefixes:
            if lower.startswith(prefix):
                return category
    return "other"


def _wav_duration(data: bytes) -> float | None:
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    position = 12
    channels = rate = bits = 0
    while position + 8 <= len(data):
        chunk = data[position:position + 4]
        (length,) = struct.unpack("<I", data[position + 4:position + 8])
        body = position + 8
        if chunk == b"fmt " and body + 16 <= len(data):
            _format, channels, rate, _byte_rate, _align, bits = struct.unpack(
                "<HHIIHH", data[body:body + 16])
        elif chunk == b"data":
            if not rate or not channels or not bits:
                return None
            return length / (rate * channels * (bits // 8))
        position = body + length + (length & 1)
    return None


def _mp3_duration(data: bytes) -> float | None:
    """Approximate: WC3 music is constant bitrate, so size / bitrate is close enough."""
    position = 0
    if data[:3] == b"ID3" and len(data) > 10:
        size = ((data[6] & 0x7F) << 21) | ((data[7] & 0x7F) << 14) | \
               ((data[8] & 0x7F) << 7) | (data[9] & 0x7F)
        position = 10 + size
    while position + 4 <= len(data) and position < 65536:
        if data[position] == 0xFF and (data[position + 1] & 0xE0) == 0xE0:
            header = data[position:position + 4]
            bitrate_index = (header[2] >> 4) & 0x0F
            rate_index = (header[2] >> 2) & 0x03
            bitrate = _MP3_BITRATES[bitrate_index] * 1000
            sample_rate = _MP3_RATES[rate_index]
            if bitrate and sample_rate:
                return (len(data) - position) * 8 / bitrate
        position += 1
    return None


def duration_seconds(internal: str, data: bytes) -> float | None:
    lower = internal.lower()
    if lower.endswith(".wav"):
        return _wav_duration(data)
    if lower.endswith(".mp3"):
        return _mp3_duration(data)
    return None


def inventory(game, category: str | None = None, needle: str = "",
              measure: bool = False) -> list[AudioFile]:
    """Every audio file in the archives, optionally filtered."""
    sizes = {}
    for archive in game.archives:
        for name, size in archive.find():
            sizes[name.lower()] = (name, size)
    out = []
    for key, (name, size) in sorted(sizes.items()):
        if not key.endswith(AUDIO_EXTENSIONS):
            continue
        kind = categorise(name)
        if category and kind != category:
            continue
        if needle and needle.lower() not in key:
            continue
        item = AudioFile(internal=name, size=size, category=kind)
        if measure:
            data = game.read(name)
            if data:
                item.duration = duration_seconds(name, data)
        out.append(item)
    return out


def summary(files: list[AudioFile]) -> dict:
    """Counts, bytes and play time per category."""
    result: dict[str, dict] = {}
    for item in files:
        bucket = result.setdefault(item.category, {"files": 0, "bytes": 0, "seconds": 0.0})
        bucket["files"] += 1
        bucket["bytes"] += item.size
        if item.duration:
            bucket["seconds"] += item.duration
    return result


def extract(game, out: Path, category: str | None = None, needle: str = "",
            limit: int = 0, progress=None) -> dict:
    """Copy the audio files out, keeping the archive layout."""
    files = inventory(game, category=category, needle=needle)
    if limit:
        files = files[:limit]
    out = Path(out)
    stats = {"written": 0, "bytes": 0, "failed": 0, "count": len(files)}
    for index, item in enumerate(files, 1):
        try:
            data = game.read(item.internal)
            if not data:
                stats["failed"] += 1
                continue
            target = out / Path(item.internal.replace("\\", "/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            stats["written"] += 1
            stats["bytes"] += len(data)
        except OSError:
            stats["failed"] += 1
        if progress is not None and (index % 100 == 0 or index == len(files)):
            progress(index, len(files), item.internal)
    return stats
