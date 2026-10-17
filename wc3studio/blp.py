"""Decoder for Blizzard BLP1 textures (the format used by Warcraft III).

BLP1 layout:
    0   char[4]  "BLP1"
    4   u32      compression   (0 = JPEG, 1 = paletted)
    8   u32      flags         (0x08 = alpha channel present)
    12  u32      width
    16  u32      height
    20  u32      picture_type
    24  u32      picture_sub_type
    28  u32[16]  mip offsets (absolute file offsets)
    92  u32[16]  mip sizes
    --- paletted (compression 1) ---
    156 u8[1024] palette (BGRA, 256 entries); each mip is indices then optional alpha
    --- JPEG (compression 0) ---
    156 u32      jpeg header size N
    160 u8[N]    shared JPEG header: SOI, APP0, COM, DQT, DHT ... ending right after the
                 SOF0 marker (FF C0), whose length field is the first two bytes of the
                 mip. A full JPEG is therefore header + mip data.
"""
from __future__ import annotations

import io
import struct

from PIL import Image

PALETTE_OFFSET = 156
PALETTE_SIZE = 1024


class BlpError(Exception):
    pass


def header(data: bytes) -> dict:
    if data[:4] != b"BLP1":
        raise BlpError(f"not a BLP1 file (magic {data[:4]!r})")
    magic, comp, flags, w, h, ptype, psub = struct.unpack_from("<4sIIIIII", data, 0)
    offsets = struct.unpack_from("<16I", data, 28)
    sizes = struct.unpack_from("<16I", data, 92)
    return dict(compression=comp, flags=flags, width=w, height=h,
                picture_type=ptype, picture_sub_type=psub,
                offsets=offsets, sizes=sizes)


def decode(data: bytes) -> Image.Image:
    """Decode the largest mip of a BLP1 texture to an RGBA PIL image."""
    hdr = header(data)
    w, h = hdr["width"], hdr["height"]
    if w == 0 or h == 0 or w > 8192 or h > 8192:
        raise BlpError(f"implausible BLP dimensions {w}x{h}")

    off, size = hdr["offsets"][0], hdr["sizes"][0]
    if off == 0 or size == 0:
        raise BlpError("no mip 0 in BLP")
    if off + size > len(data):
        raise BlpError("mip 0 runs past end of file")
    mip = data[off:off + size]

    if hdr["compression"] == 0:  # JPEG
        if len(data) < 160:
            raise BlpError("truncated JPEG header block")
        hsize = struct.unpack_from("<I", data, 156)[0]
        if not (0 < hsize <= len(data) - 160):
            raise BlpError(f"implausible JPEG header size {hsize}")
        shared = data[160:160 + hsize]
        if shared[:2] != b"\xff\xd8":
            raise BlpError("JPEG header block does not start with SOI")
        try:
            img = Image.open(io.BytesIO(shared + mip)).convert("RGBA")
        except Exception as exc:
            raise BlpError(f"JPEG decode failed: {exc}") from exc
        if img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)
        return img

    if hdr["compression"] != 1:
        raise BlpError(f"unsupported BLP compression {hdr['compression']}")

    n = w * h
    if len(data) < PALETTE_OFFSET + PALETTE_SIZE:
        raise BlpError("truncated palette")
    palette = data[PALETTE_OFFSET:PALETTE_OFFSET + PALETTE_SIZE]

    if len(mip) < n:
        raise BlpError(f"paletted mip too short: {len(mip)} < {n}")

    has_alpha = bool(hdr["flags"] & 0x08) and len(mip) >= 2 * n

    pal = bytearray(256 * 4)
    for i in range(256):
        b, g, r, _a = palette[i * 4:i * 4 + 4]
        pal[i * 4 + 0] = r
        pal[i * 4 + 1] = g
        pal[i * 4 + 2] = b
        pal[i * 4 + 3] = 255

    img = Image.frombytes("P", (w, h), bytes(mip[:n]))
    img.putpalette(bytes(pal), rawmode="RGBA")
    img = img.convert("RGBA")

    if has_alpha:
        alpha = Image.frombytes("L", (w, h), bytes(mip[n:2 * n]))
        img.putalpha(alpha)
    return img
