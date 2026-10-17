"""The user interface layer: FDF frame definitions plus the UI artwork.

``.fdf`` files are the interface's layout source. They are text: a tree of ``Frame``
blocks with ``Key value`` properties, child blocks such as ``Texture { File "...",
TexCoord ... }``, ``IncludeFile`` references to other FDFs, and ``StringList`` blocks
that hold the localised UI text.

The parser below is deliberately tolerant: unknown statements are recorded as warnings
instead of raising, because the interface files vary a lot between patches.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

__all__ = ["Frame", "FdfDocument", "parse_fdf", "UiLibrary", "IMAGE_EXTENSIONS"]

IMAGE_EXTENSIONS = (".blp", ".tga", ".jpg", ".jpeg", ".png")

# Frame "TYPE" "NAME" [INHERITS [WITHCHILDREN] "Template"]
_FRAME_HEADER = re.compile(
    r'^Frame\s*"([^"]*)"\s*"([^"]*)"\s*(?:INHERITS\s+(?:WITHCHILDREN\s+)?"([^"]*)")?\s*$')
# WORD ["NAME"] [INHERITS [WITHCHILDREN] "Template"]  -- String/Texture/Layer/...
_BLOCK_HEADER = re.compile(
    r'^(\w+)\s*(?:"([^"]*)")?\s*(?:INHERITS\s+(?:WITHCHILDREN\s+)?"([^"]*)")?\s*$')
_PROPERTY = re.compile(r'^(\w+)\s*(.*)$')


@dataclass
class Frame:
    """One FDF block: a frame, a texture layer, a string definition..."""

    kind: str = ""
    name: str = ""
    inherits: str = ""
    properties: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    source: str = ""
    line: int = 0

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def property(self, *names: str, default: str = "") -> str:
        for name in names:
            for key, value in self.properties.items():
                if key.lower() == name.lower():
                    return value
        return default

    def texture_references(self) -> list:
        """Asset paths this block points at (decorated logical names are skipped)."""
        out = []
        for value in self.properties.values():
            for token in str(value).split(","):
                token = token.strip().strip('"')
                if token.lower().endswith(IMAGE_EXTENSIONS) or ("\\" in token and "." in token):
                    if token not in out:
                        out.append(token)
        return out

    def decorated_names(self) -> list:
        """Logical texture names (``File "ConsoleTexture01"``) that the engine resolves."""
        out = []
        for key, value in self.properties.items():
            if key.lower() not in ("file", "background", "texture"):
                continue
            token = str(value).strip().strip('"')
            if not token or token.lower().endswith(IMAGE_EXTENSIONS) or "\\" in token:
                continue
            if token not in out:
                out.append(token)
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Frame {self.kind} {self.name!r} props={len(self.properties)} children={len(self.children)}>"


@dataclass
class FdfDocument:
    path: str = ""
    includes: list = field(default_factory=list)
    frames: list = field(default_factory=list)
    strings: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def frames_named(self) -> dict:
        return {frame.name.lower(): frame for frame in self.frames if frame.name}

    def all_frames(self) -> list:
        out = []
        for frame in self.frames:
            out.extend(frame.walk())
        return out

    def texture_references(self) -> list:
        out = []
        for frame in self.all_frames():
            for reference in frame.texture_references():
                if reference not in out:
                    out.append(reference)
        return out


def _prepare(text: str):
    """Split an FDF file into (statement, source line) segments.

    Comments are removed without touching quoted strings, and braces become their own
    segments so the parser can stay line oriented while keeping real line numbers.
    """
    segments: list[tuple[str, int]] = []
    in_block_comment = False
    for number, raw in enumerate(text.splitlines(), 1):
        buffer = ""
        index = 0
        in_string = False
        length = len(raw)
        while index < length:
            char = raw[index]
            if in_block_comment:
                if raw.startswith("*/", index):
                    in_block_comment = False
                    index += 2
                else:
                    index += 1
                continue
            if in_string:
                buffer += char
                if char == '"':
                    in_string = False
                index += 1
                continue
            if char == '"':
                in_string = True
                buffer += char
                index += 1
                continue
            if raw.startswith("//", index):
                break
            if raw.startswith("/*", index):
                in_block_comment = True
                index += 2
                continue
            if char in "{}":
                if buffer.strip():
                    segments.append((buffer, number))
                segments.append((char, number))
                buffer = ""
                index += 1
                continue
            buffer += char
            index += 1
        if buffer.strip():
            segments.append((buffer, number))
    return segments


def parse_fdf(text: str, source: str = "") -> FdfDocument:
    """Parse one FDF file into a document with a frame tree and its string table.

    Whether ``Foo "bar"`` opens a block or sets a property is decided by look-ahead:
    only a following ``{`` makes it a block, so ``Text "MENU_TITLE",`` stays a property
    while ``Texture { ... }`` becomes a child.
    """
    document = FdfDocument(path=source)
    segments = _prepare(text)
    stack: list[Frame] = []
    root = Frame(kind="__root__")

    def current() -> Frame:
        return stack[-1] if stack else root

    def next_is_brace(position: int) -> bool:
        return position + 1 < len(segments) and segments[position + 1][0] == "{"

    expect_brace = False
    for position, (raw, number) in enumerate(segments):
        line = raw.strip().rstrip(",").strip()
        if not line:
            continue

        if line == "{":
            if expect_brace:
                expect_brace = False
            else:
                document.warnings.append(f"{source}:{number}: '{{' without a block header")
                stack.append(Frame(kind="__unknown__", source=source, line=number))
            continue

        if line == "}":
            if stack:
                block = stack.pop()
                if block.kind == "StringList":
                    document.strings.update(block.properties)
            else:
                document.warnings.append(f"{source}:{number}: unbalanced '}}'")
            continue

        opens_block = next_is_brace(position)

        if opens_block:
            frame = None
            match = _FRAME_HEADER.match(line)
            if match:
                frame = Frame(kind=match.group(1), name=match.group(2),
                              inherits=match.group(3) or "", source=source, line=number)
            else:
                match = _BLOCK_HEADER.match(line)
                if match:
                    frame = Frame(kind=match.group(1), name=match.group(2) or "",
                                  inherits=match.group(3) or "", source=source, line=number)
            if frame is None:
                document.warnings.append(f"{source}:{number}: cannot parse block header {line[:40]!r}")
                continue
            if stack:
                current().children.append(frame)
            else:
                document.frames.append(frame)
            stack.append(frame)
            expect_brace = True
            continue

        # a statement that is not a block
        if not stack:
            if line.startswith("IncludeFile"):
                include = line[len("IncludeFile"):].strip().strip('"')
                if include:
                    document.includes.append(include)
                continue
            document.warnings.append(f"{source}:{number}: stray line {line[:40]!r}")
            continue

        match = _PROPERTY.match(line)
        if match:
            key, value = match.group(1), match.group(2).strip()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            current().properties[key] = value
        else:
            document.warnings.append(f"{source}:{number}: cannot parse {line[:40]!r}")

    if stack:
        document.warnings.append(f"{source}: {len(stack)} block(s) left open at end of file")
    return document


class UiLibrary:
    """All FDF documents of a game plus the UI artwork, with template resolution."""

    def __init__(self, game):
        self.game = game
        self._documents: dict[str, FdfDocument] = {}
        self._index: dict[str, Frame] | None = None
        self._strings: dict[str, str] | None = None
        self._files: list[tuple[str, int]] | None = None

    # -- documents --------------------------------------------------------
    def documents(self, needle: str = "") -> list[str]:
        if self._files is None:
            seen: dict[str, int] = {}
            for archive in self.game.archives:
                for name, size in archive.find():
                    if name.lower().endswith(".fdf"):
                        seen.setdefault(name.lower(), name)
            self._files = sorted((name, 0) for name in seen.values())
        names = [name for name, _ in self._files]
        if not needle:
            return names
        wanted = needle.lower()
        return [name for name in names if wanted in name.lower()]

    def load(self, path: str) -> FdfDocument:
        key = path.lower()
        if key not in self._documents:
            data = self.game.read(path)
            if data is None:
                raise FileNotFoundError(f"FDF not found: {path}")
            from .tables import decode_text
            self._documents[key] = parse_fdf(decode_text(data), path)
        return self._documents[key]

    # -- frames -----------------------------------------------------------
    def index(self) -> dict:
        """Every frame of every document by lower-case name (first definition wins)."""
        if self._index is None:
            index: dict[str, Frame] = {}
            for path in self.documents():
                try:
                    document = self.load(path)
                except FileNotFoundError:
                    continue
                for frame in document.all_frames():
                    if frame.name:
                        index.setdefault(frame.name.lower(), frame)
            self._index = index
        return self._index

    def resolve_reference(self, reference: str) -> str | None:
        """Real internal path for an FDF reference, or None.

        Interface files sometimes name a ``.mdl`` where the archive holds ``.mdx``, and
        some references point at textures that only newer patches shipped, so a failed
        exact lookup falls back to a case-insensitive basename search.
        """
        if not reference:
            return None
        if self.game.read(reference) is not None:
            return reference
        if reference.lower().endswith(".mdl"):
            alternative = reference[:-4] + ".mdx"
            if self.game.read(alternative) is not None:
                return alternative
        base = reference.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        for name in self.game.names():
            if name.lower().endswith(base):
                return name
        return None

    def resolve(self, name: str) -> dict:
        """Merged properties of a frame following its INHERITS chain (root first)."""
        index = self.index()
        frame = index.get(name.lower())
        if frame is None:
            raise KeyError(f"no frame named {name!r}")
        chain = [frame]
        seen = {frame.name.lower()}
        while chain[-1].inherits:
            parent = index.get(chain[-1].inherits.lower())
            if parent is None or parent.name.lower() in seen:
                break
            seen.add(parent.name.lower())
            chain.append(parent)
        merged: dict[str, str] = {}
        origin: dict[str, str] = {}
        for item in reversed(chain):
            for key, value in item.properties.items():
                merged[key] = value
                origin[key] = item.name or item.kind
        textures = []
        decorated = []
        for block in frame.walk():
            for reference in block.texture_references():
                resolved = self.resolve_reference(reference)
                if (reference, resolved) not in textures:
                    textures.append((reference, resolved))
            for name_ in block.decorated_names():
                if name_ not in decorated:
                    decorated.append(name_)
        return {"name": frame.name, "kind": frame.kind, "inherits": frame.inherits,
                "chain": [item.name for item in reversed(chain)],
                "properties": merged, "origin": origin, "textures": textures,
                "decorated": decorated,
                "missing_textures": [ref for ref, hit in textures if not hit],
                "source": frame.source}

    def frames(self, needle: str = "", limit: int = 0) -> list:
        items = []
        for name, frame in sorted(self.index().items()):
            if needle and needle.lower() not in name and needle.lower() not in frame.kind.lower():
                continue
            items.append(frame)
        return items[:limit] if limit else items

    # -- strings ----------------------------------------------------------
    def strings(self) -> dict:
        """Localised UI strings from every StringList block."""
        if self._strings is None:
            merged: dict[str, str] = {}
            for path in self.documents():
                try:
                    document = self.load(path)
                except FileNotFoundError:
                    continue
                for key, value in document.strings.items():
                    merged.setdefault(key, value)
            self._strings = merged
        return self._strings

    # -- assets -----------------------------------------------------------
    def assets(self, needle: str = "") -> list:
        """UI artwork under ``UI\\`` with size and kind."""
        found: dict[str, int] = {}
        for archive in self.game.archives:
            for name, size in archive.find():
                lower = name.lower()
                if lower.startswith("ui\\") and lower.endswith(IMAGE_EXTENSIONS):
                    found.setdefault(lower, size)
        items = [{"path": self.game.canonical(name), "size": size,
                  "name": self.game.canonical(name).replace("\\", "/").rsplit("/", 1)[-1]}
                 for name, size in found.items()]
        items.sort(key=lambda item: item["path"].lower())
        if needle:
            wanted = needle.lower()
            items = [item for item in items if wanted in item["path"].lower()]
        return items

    def dump_assets(self, resources, out_dir, needle: str = "", limit: int = 0) -> dict:
        """Convert UI artwork to PNG, mirroring the internal folder structure."""
        out_dir = Path(out_dir)
        items = self.assets(needle)
        if limit:
            items = items[:limit]
        written = failed = 0
        total = 0
        for item in items:
            image = resources.image(item["path"])
            if image is None:
                failed += 1
                continue
            target = out_dir / (item["path"].replace("\\", "/") + ".png")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target)
            written += 1
            total += item["size"]
        return {"written": written, "failed": failed, "bytes": total,
                "out": str(out_dir), "count": len(items)}


def build_contact_sheet(images: list, labels: list, tile: int = 128, columns: int = 8,
                        background=(22, 24, 30)) -> Image.Image:
    """Grid of thumbnails with labels, used for UI artwork previews."""
    if not images:
        return Image.new("RGB", (tile, tile), background)
    font = None
    for candidate in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, max(9, tile // 11))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    label_height = max(14, tile // 8)
    rows = (len(images) + columns - 1) // columns
    padding = 6
    sheet = Image.new("RGB", (columns * (tile + padding) + padding,
                              rows * (tile + label_height + padding) + padding), background)
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(images):
        x = padding + (index % columns) * (tile + padding)
        y = padding + (index // columns) * (tile + label_height + padding)
        thumb = image.copy()
        thumb.thumbnail((tile, tile))
        offset = ((tile - thumb.width) // 2, (tile - thumb.height) // 2)
        sheet.paste(thumb, (x + offset[0], y + offset[1]))
        draw.text((x, y + tile + 2), labels[index][:28], fill=(220, 224, 232), font=font)
    return sheet
