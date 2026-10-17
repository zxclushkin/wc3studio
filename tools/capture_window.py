"""Capture a window by (part of) its title, including child Toplevel windows.

    python tools/capture_window.py "Bundle everything" out.png

Uses PrintWindow with PW_RENDERFULLCONTENT so the capture works without bringing the
window to the foreground.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes as wt

from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def windows_matching(needle: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    needle = needle.lower()

    def callback(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value
        if needle in title.lower() and user32.IsWindowVisible(hwnd):
            found.append((hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def capture(hwnd: int, path: str) -> tuple[int, int]:
    rect = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise SystemExit("window has no client area")

    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    user32.PrintWindow(hwnd, memory_dc, PW_RENDERFULLCONTENT)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                    ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                    ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                    ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height          # top-down
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)

    image.save(path)
    return image.size


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    needle, path = argv[1], argv[2]
    candidates = windows_matching(needle)
    if not candidates:
        print(f"window not found: {needle}")
        return 1
    hwnd, title = candidates[0]
    size = capture(hwnd, path)
    print(f"captured {title!r} -> {path} ({size[0]}x{size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
