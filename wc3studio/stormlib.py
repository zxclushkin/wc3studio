"""StormLib binding (ctypes) plus the archive model used by the rest of wc3studio.

StormLib is Ladislav Zezula's MPQ library (MIT licence, https://github.com/ladislav-zezula/StormLib).
A prebuilt x64 DLL ships in ``wc3studio/vendor``; set ``WC3STUDIO_STORMLIB`` to use another one.
"""
from __future__ import annotations

import ctypes as C
import os
import sys
from pathlib import Path

__all__ = ["StormLib", "Archive", "Game", "StormLibError", "find_game_dirs", "ARCHIVE_ORDER"]

ARCHIVE_ORDER = ["War3.mpq", "War3Patch.mpq", "War3x.mpq", "War3xlocal.mpq"]

STREAM_FLAG_READ_ONLY = 0x00000100

# SFileGetFileInfo classes we use
INFO_NUMBER_OF_FILES = 36
INFO_ARCHIVE_SIZE = 32
INFO_FLAGS = 39

MAX_PATH = 260


class StormLibError(RuntimeError):
    pass


class _FindData(C.Structure):
    _fields_ = [
        ("cFileName", C.c_char * MAX_PATH),
        ("szPlainName", C.c_char_p),
        ("dwHashIndex", C.c_uint32),
        ("dwBlockIndex", C.c_uint32),
        ("dwFileSize", C.c_uint32),
        ("dwFileFlags", C.c_uint32),
        ("dwCompSize", C.c_uint32),
        ("dwFileTimeLo", C.c_uint32),
        ("dwFileTimeHi", C.c_uint32),
        ("lcLocale", C.c_uint32),
    ]


def _candidate_dll_paths() -> list[Path]:
    here = Path(__file__).resolve().parent
    out = []
    env = os.environ.get("WC3STUDIO_STORMLIB")
    if env:
        out.append(Path(env))
    out += [
        here / "vendor" / "StormLib.dll",
        here / "vendor" / "stormlib" / "x64" / "StormLib.dll",
        Path.cwd() / "StormLib.dll",
        Path(sys.executable).parent / "StormLib.dll",
    ]
    return out


def find_stormlib() -> Path:
    for path in _candidate_dll_paths():
        if path.is_file():
            return path
    tried = "\n  ".join(str(p) for p in _candidate_dll_paths())
    raise StormLibError(
        "StormLib.dll not found. Put a 64-bit StormLib.dll next to the package or set "
        f"WC3STUDIO_STORMLIB.\nLooked in:\n  {tried}"
    )


class StormLib:
    """Loads StormLib once and exposes the handful of functions we need."""

    _instance: "StormLib | None" = None

    def __init__(self, dll_path: str | Path | None = None):
        self.path = Path(dll_path) if dll_path else find_stormlib()
        if C.sizeof(C.c_void_p) != 8 and "x64" in str(self.path).lower():
            raise StormLibError("a 64-bit Python is required for the bundled x64 StormLib.dll")
        self.dll = C.WinDLL(str(self.path), use_last_error=True)
        self._bind()

    @classmethod
    def instance(cls) -> "StormLib":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _bind(self):
        d = self.dll
        # This build is Unicode: TCHAR parameters are wchar_t*, while names that live
        # inside the archive (SFileOpenFileEx, find masks, SFILE_FIND_DATA) stay char*.
        d.SFileOpenArchive.argtypes = [C.c_wchar_p, C.c_uint32, C.c_uint32, C.POINTER(C.c_void_p)]
        d.SFileOpenArchive.restype = C.c_bool
        d.SFileCloseArchive.argtypes = [C.c_void_p]
        d.SFileCloseArchive.restype = C.c_bool
        d.SFileGetFileInfo.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)]
        d.SFileGetFileInfo.restype = C.c_bool
        d.SFileOpenFileEx.argtypes = [C.c_void_p, C.c_char_p, C.c_uint32, C.POINTER(C.c_void_p)]
        d.SFileOpenFileEx.restype = C.c_bool
        d.SFileGetFileSize.argtypes = [C.c_void_p, C.POINTER(C.c_uint32)]
        d.SFileGetFileSize.restype = C.c_uint32
        d.SFileReadFile.argtypes = [C.c_void_p, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.c_void_p]
        d.SFileReadFile.restype = C.c_bool
        d.SFileCloseFile.argtypes = [C.c_void_p]
        d.SFileCloseFile.restype = C.c_bool
        d.SFileFindFirstFile.argtypes = [C.c_void_p, C.c_char_p, C.POINTER(_FindData), C.c_wchar_p]
        d.SFileFindFirstFile.restype = C.c_void_p
        d.SFileFindNextFile.argtypes = [C.c_void_p, C.POINTER(_FindData)]
        d.SFileFindNextFile.restype = C.c_bool
        d.SFileFindClose.argtypes = [C.c_void_p]
        d.SFileFindClose.restype = C.c_bool

    def version(self) -> str:
        for name in ("SFileGetVersionString", "SFileGetVersion"):
            fn = getattr(self.dll, name, None)
            if fn is None:
                continue
            fn.restype = C.c_char_p
            try:
                value = fn()
            except Exception:  # noqa: BLE001
                continue
            if value:
                return value.decode("latin-1")
        return "unknown"


class Archive:
    """A single opened MPQ archive (read-only)."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.name = self.path.name
        self.lib = StormLib.instance()
        self.handle = C.c_void_p()
        self._listfile: list[str] | None = None
        if not self.path.is_file():
            raise StormLibError(f"archive not found: {self.path}")
        if not self.lib.dll.SFileOpenArchive(str(self.path), 0, STREAM_FLAG_READ_ONLY,
                                             C.byref(self.handle)):
            raise StormLibError(f"StormLib could not open {self.path} (error {C.get_last_error()})")

    # -- info -------------------------------------------------------------
    def _info(self, info_class: int) -> int | None:
        out = C.c_uint32(0)
        got = C.c_uint32(0)
        ok = self.lib.dll.SFileGetFileInfo(self.handle, info_class, C.byref(out),
                                           C.sizeof(out), C.byref(got))
        return out.value if ok else None

    @property
    def file_count(self) -> int | None:
        return self._info(INFO_NUMBER_OF_FILES)

    @property
    def size(self) -> int:
        return self.path.stat().st_size

    # -- reading ----------------------------------------------------------
    def read(self, name: str) -> bytes | None:
        handle = C.c_void_p()
        if not self.lib.dll.SFileOpenFileEx(self.handle, name.encode("utf-8"), 0, C.byref(handle)):
            return None
        try:
            high = C.c_uint32(0)
            size = self.lib.dll.SFileGetFileSize(handle, C.byref(high))
            if size == 0xFFFFFFFF:
                return None
            if size == 0:
                return b""
            buffer = C.create_string_buffer(size)
            read = C.c_uint32(0)
            if not self.lib.dll.SFileReadFile(handle, buffer, size, C.byref(read), None):
                return None
            return buffer.raw[: read.value]
        finally:
            self.lib.dll.SFileCloseFile(handle)

    def has(self, name: str) -> bool:
        return self.read(name) is not None

    def listfile(self) -> list[str]:
        """Names from the archive's own (listfile); cached."""
        if self._listfile is None:
            data = self.read("(listfile)")
            self._listfile = [n.strip() for n in data.decode("utf-8", "replace").splitlines()
                              if n.strip()] if data else []
        return self._listfile

    def find(self) -> list[tuple[str, int]]:
        """Enumerate through the hash table (works even without a listfile)."""
        out: list[tuple[str, int]] = []
        data = _FindData()
        handle = self.lib.dll.SFileFindFirstFile(self.handle, b"*", C.byref(data), None)
        if not handle:
            return out
        try:
            while True:
                out.append((data.cFileName.decode("latin-1"), data.dwFileSize))
                if not self.lib.dll.SFileFindNextFile(handle, C.byref(data)):
                    break
        finally:
            self.lib.dll.SFileFindClose(handle)
        return out

    def extract(self, name: str, out_root: str | os.PathLike) -> int | None:
        """Write one internal file to ``out_root`` keeping its internal path."""
        data = self.read(name)
        if data is None:
            return None
        dest = Path(out_root) / name.replace("\\", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    # -- lifecycle --------------------------------------------------------
    def close(self):
        if self.handle:
            self.lib.dll.SFileCloseArchive(self.handle)
            self.handle = C.c_void_p()

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        return f"<Archive {self.name} files={self.file_count}>"


class Game:
    """The archive chain of an installation, highest priority first.

    Later archives patch earlier ones, so a lookup walks the list in order.
    """

    def __init__(self, archives: list[Archive], root: Path | None = None):
        self.archives = archives
        self.root = root
        self._index: dict[str, str] | None = None
        self._cache: dict[str, bytes | None] = {}
        self._names: list[str] | None = None
        self._sizes: dict[str, int] | None = None

    @classmethod
    def open(cls, game_dir: str | os.PathLike) -> "Game":
        root = Path(game_dir)
        if not root.is_dir():
            raise StormLibError(f"not a directory: {root}")
        archives = []
        for name in reversed(ARCHIVE_ORDER):  # highest priority first
            path = root / name
            if path.is_file():
                archives.append(Archive(path))
        if not archives:
            raise StormLibError(f"no Warcraft III MPQ archives found in {root}")
        return cls(archives, root)

    @classmethod
    def autodetect(cls) -> "Game":
        errors = []
        for root in find_game_dirs():
            try:
                return cls.open(root)
            except StormLibError as exc:
                errors.append(str(exc))
        raise StormLibError(
            "no Warcraft III installation found; pass a path with --game "
            "(the folder must contain War3.mpq / War3x.mpq)"
        )

    # -- lookup -----------------------------------------------------------
    @property
    def index(self) -> dict[str, str]:
        """case-folded internal name -> canonical name from the listfiles."""
        if self._index is None:
            index: dict[str, str] = {}
            for archive in reversed(self.archives):  # low priority first
                for name in archive.listfile():
                    key = name.replace("/", "\\").lower()
                    index[key] = name.replace("/", "\\")
            self._index = index
        return self._index

    def canonical(self, name: str) -> str:
        norm = name.replace("/", "\\").strip()
        return self.index.get(norm.lower(), norm)

    def read(self, name: str) -> bytes | None:
        key = name.replace("/", "\\").strip().lower()
        if key in self._cache:
            return self._cache[key]
        canonical = self.canonical(name)
        data = None
        for archive in self.archives:
            data = archive.read(canonical)
            if data:
                break
        if not data and canonical != name:
            for archive in self.archives:
                data = archive.read(name)
                if data:
                    break
        self._cache[key] = data
        return data

    def names(self) -> list[str]:
        """Every internal name, deduplicated case-insensitively (cached)."""
        if self._names is None:
            seen: dict[str, str] = {}
            for archive in self.archives:
                for name in archive.listfile():
                    seen.setdefault(name.lower(), name)
            self._names = sorted(seen.values())
        return self._names

    def sizes(self) -> dict[str, int]:
        """Case-folded internal name -> file size in bytes (cached)."""
        if self._sizes is None:
            sizes: dict[str, int] = {}
            for archive in reversed(self.archives):  # later archives win
                for name, size in archive.find():
                    sizes[name.replace("/", "\\").lower()] = size
            self._sizes = sizes
        return self._sizes

    def size(self, name: str) -> int:
        """Size of one internal file, 0 when it is not in the archives."""
        return self.sizes().get(name.replace("/", "\\").lower(), 0)

    def models(self) -> list[str]:
        return [n for n in self.names() if n.lower().endswith((".mdx", ".mdl"))]

    def close(self):
        for archive in self.archives:
            archive.close()

    def __enter__(self) -> "Game":
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self) -> str:
        return f"<Game {self.root} archives={[a.name for a in self.archives]}>"


def find_game_dirs() -> list[Path]:
    """Likely Warcraft III install directories, Windows registry first."""
    found: list[Path] = []

    def add(path: str | os.PathLike | None):
        if not path:
            return
        candidate = Path(path)
        if candidate.is_dir() and candidate not in found:
            found.append(candidate)

    try:
        import winreg  # noqa: PLC0415
        keys = [
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Blizzard Entertainment\Warcraft III"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Blizzard Entertainment\Warcraft III"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Blizzard Entertainment\Warcraft III"),
        ]
        for hive, sub in keys:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    for value in ("InstallPath", "GamePath", "Path"):
                        try:
                            add(winreg.QueryValueEx(key, value)[0])
                        except OSError:
                            continue
            except OSError:
                continue
    except Exception:  # noqa: BLE001 - not Windows, or no registry access
        pass

    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.environ.get("SystemDrive", "C:") + "\\", os.path.expanduser("~")):
        if base:
            add(Path(base) / "Warcraft III")
    for drive in "CDEFGH":
        add(f"{drive}:\\Games\\Warcraft III")
    return found
