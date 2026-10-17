"""Gameplay data tables.

Warcraft III keeps its numbers in two text formats inside the MPQ:

* **.slk** - SYLK spreadsheets. One ``C;`` record per cell, the row number appears only
  when it changes, ``K`` carries the value. Used for units, abilities, buffs, upgrades,
  doodads, terrain art, sounds.
* **.txt** - INI-like files with ``[section]`` headers, holding names, tooltips and
  per-object overrides. The localisation lives here (``*Strings.txt``), which is why
  this install has Russian unit names.

Both are turned into :class:`Table` objects with named columns.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Table", "parse_sylk", "parse_ini", "TableDatabase", "decode_text"]

ENCODINGS = ("utf-8-sig", "cp1251", "latin-1")


def decode_text(data: bytes) -> str:
    """WC3 text files are UTF-8 (often with a BOM); Russian localisation is cp1251 in places."""
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


@dataclass
class Table:
    """A parsed data table: named columns plus rows of strings."""

    name: str = ""
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    kind: str = "slk"
    source: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def index_of(self, column: str) -> int:
        wanted = column.strip().lower()
        for index, name in enumerate(self.columns):
            if name.strip().lower() == wanted:
                return index
        raise KeyError(f"{self.name}: no column {column!r} (has {', '.join(self.columns[:12])}...)")

    def column(self, name: str) -> list:
        index = self.index_of(name)
        return [row[index] if index < len(row) else "" for row in self.rows]

    def as_dicts(self, key: str | None = None) -> list:
        key_index = self.index_of(key) if key else None
        out = []
        for row in self.rows:
            record = {column: (row[i] if i < len(row) else "") for i, column in enumerate(self.columns)}
            if key_index is not None:
                record["_key"] = row[key_index] if key_index < len(row) else ""
            out.append(record)
        return out

    def find_row(self, key: str) -> list | None:
        wanted = key.strip().lower()
        for row in self.rows:
            if row and row[0].strip().lower() == wanted:
                return row
        return None

    def filter(self, predicate) -> "Table":
        """Keep rows matching predicate(row, record)."""
        kept = []
        for row in self.rows:
            record = {column: (row[i] if i < len(row) else "") for i, column in enumerate(self.columns)}
            if predicate(row, record):
                kept.append(row)
        return Table(self.name, list(self.columns), kept, self.kind, self.source)

    def where(self, **conditions) -> "Table":
        """Keep rows where the given columns equal the given values (case-insensitive)."""
        indexes = {self.index_of(column): str(value).strip().lower() for column, value in conditions.items()}

        def match(row, _record):
            for index, value in indexes.items():
                cell = row[index] if index < len(row) else ""
                if cell.strip().lower() != value:
                    return False
            return True

        return self.filter(match)

    def select(self, columns: list) -> "Table":
        indexes = [self.index_of(column) for column in columns]
        rows = [[row[i] if i < len(row) else "" for i in indexes] for row in self.rows]
        return Table(self.name, [self.columns[i] for i in indexes], rows, self.kind, self.source)

    def to_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(self.columns)
        writer.writerows(self.rows)
        return buffer.getvalue()

    def to_json(self) -> str:
        return json.dumps({"name": self.name, "kind": self.kind, "source": self.source,
                           "columns": self.columns, "rows": self.rows},
                          ensure_ascii=False, indent=1)

    def write(self, path: str | Path, fmt: str = "csv") -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_csv() if fmt == "csv" else self.to_json(), encoding="utf-8")
        return path

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Table {self.name} {self.row_count}x{self.column_count} {self.kind}>"


# --------------------------------------------------------------------------- parsers

_CELL = re.compile(r"^C;")
_SYLK_VALUE = re.compile(r"K(.*)$")


def parse_sylk(text: str, name: str = "", source: str = "") -> Table:
    """Parse the SYLK flavour Blizzard uses.

    A cell is ``C;X<col>[;Y<row>];K<value>``. ``Y`` is only present when the row
    changes, which is the detail that produces "one column" tables if you ignore it.
    """
    grid: dict[int, dict[int, str]] = {}
    current_row = 0
    for raw in text.splitlines():
        line = raw.strip("\r")
        if not _CELL.match(line):
            continue
        column = None
        value = ""
        for part in line.split(";")[1:]:
            if part.startswith("X"):
                try:
                    column = int(part[1:])
                except ValueError:
                    column = None
            elif part.startswith("Y"):
                try:
                    current_row = int(part[1:])
                except ValueError:
                    current_row = 0
            elif part.startswith("K"):
                value = part[1:]
        if column is None or not current_row:
            continue
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].replace('""', '"')
        grid.setdefault(current_row, {})[column] = value

    if not grid:
        return Table(name, [], [], "slk", source)

    width = max(max(row) for row in grid.values())
    height = max(grid)
    columns = [grid.get(1, {}).get(column, f"col{column}") or f"col{column}"
               for column in range(1, width + 1)]
    rows = [[grid.get(index, {}).get(column, "") for column in range(1, width + 1)]
            for index in range(2, height + 1)]
    return Table(name, columns, rows, "slk", source)


_INI_SECTION = re.compile(r"^\[(.+?)\]\s*$")
_INI_PAIR = re.compile(r"^([^=]+?)\s*=\s*(.*)$")


def parse_ini(text: str, name: str = "", source: str = "") -> Table:
    """Parse the ``[section]`` / ``key=value`` files (strings, object overrides)."""
    records: list[dict[str, str]] = []
    order: list[str] = ["section"]
    current = ""
    record: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//") or line.startswith(";"):
            continue
        section = _INI_SECTION.match(line)
        if section:
            current = section.group(1).strip()
            record = {"section": current}
            records.append(record)
            continue
        pair = _INI_PAIR.match(line)
        if not pair:
            continue
        if record is None:
            record = {"section": current}
            records.append(record)
        key = pair.group(1).strip()
        value = pair.group(2).strip()
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        record[key] = value
        if key not in order:
            order.append(key)

    if not records:
        return Table(name, [], [], "ini", source)
    rows = [[record.get(column, "") for column in order] for record in records]
    return Table(name, order, rows, "ini", source)


# --------------------------------------------------------------------------- database

UNIT_TABLES = {
    "balance": r"Units\UnitBalance.slk",
    "data": r"Units\UnitData.slk",
    "weapons": r"Units\UnitWeapons.slk",
    "abilities": r"Units\UnitAbilities.slk",
}

# column name -> (source table, column in that table)
UNIT_COLUMNS = [
    ("id", "data", "unitID"),
    ("name", None, None),                 # filled from the localisation
    ("race", "data", "race"),
    ("type", "data", "type"),
    ("level", "balance", "level"),
    ("hp", "balance", "realHP"),
    ("mana", "balance", "manaN"),
    ("armor", "balance", "def"),
    ("speed", "balance", "spd"),
    ("damage", "weapons", "avgdmg1"),
    ("dps", "weapons", "DPS"),
    ("range", "weapons", "rangeN1"),
    ("gold", "balance", "goldcost"),
    ("lumber", "balance", "lumbercost"),
    ("food", "balance", "fused"),
    ("buildtime", "balance", "bldtm"),
    ("sight", "balance", "sight"),
    ("str", "balance", "STR"),
    ("agi", "balance", "AGI"),
    ("int", "balance", "INT"),
    ("is_building", "balance", "isbldg"),
    ("move_type", "data", "movetp"),
    ("abilities", "abilities", "abilList"),
    ("hero_abilities", "abilities", "heroAbilList"),
]

STRING_FILES = [
    r"Units\UnitStrings.txt",
    r"Units\CampaignUnitStrings.txt",
    r"Units\HumanUnitStrings.txt",
    r"Units\OrcUnitStrings.txt",
    r"Units\UndeadUnitStrings.txt",
    r"Units\NightElfUnitStrings.txt",
    r"Units\NeutralUnitStrings.txt",
    r"Units\ItemStrings.txt",
]


class TableDatabase:
    """Loads data tables out of an opened game and joins the unit tables."""

    def __init__(self, game):
        self.game = game
        self._tables: dict[str, Table] = {}
        self._strings: dict[str, dict[str, str]] | None = None
        self._names: list[str] | None = None

    # -- discovery --------------------------------------------------------
    def names(self, needle: str = "") -> list[str]:
        if self._names is None:
            found = []
            for internal in self.game.names():
                if internal.lower().endswith((".slk", ".txt")):
                    found.append(internal)
            self._names = sorted(found)
        if not needle:
            return list(self._names)
        wanted = needle.lower()
        return [name for name in self._names if wanted in name.lower()]

    def has(self, name: str) -> bool:
        try:
            return bool(self.load(name))
        except (FileNotFoundError, LookupError):
            return False

    # -- loading ----------------------------------------------------------
    def load(self, name: str) -> Table:
        key = name.lower()
        if key in self._tables:
            return self._tables[key]
        internal = self.game.canonical(name)
        if not internal.lower().endswith((".slk", ".txt")):
            internal = self._match_table_name(name)
        data = self.game.read(internal)
        if data is None:
            raise FileNotFoundError(f"table not found: {name}")
        text = decode_text(data)
        short = internal.replace("\\", "/").rsplit("/", 1)[-1]
        if internal.lower().endswith(".slk"):
            table = parse_sylk(text, short, internal)
        else:
            table = parse_ini(text, short, internal)
        self._tables[key] = table
        return table

    def _match_table_name(self, name: str) -> str:
        """Allow 'UnitBalance' or 'unitbalance.slk' instead of a full internal path.

        The archives hold several variants of the same table (``Units\\UnitBalance.slk``
        plus ``Custom_V0``, ``Custom_V1`` and ``Melee_V0`` copies), so the shortest path
        wins: that is the base game's table.
        """
        wanted = name.replace("/", "\\").strip().lower()
        candidates = self.names(wanted)
        if not candidates:
            raise FileNotFoundError(f"table not found: {name}")
        stem = wanted.rsplit(".", 1)[0].replace("\\", "/").rsplit("/", 1)[-1]
        exact_stem = [candidate for candidate in candidates
                      if candidate.replace("\\", "/").rsplit("/", 1)[-1].lower().rsplit(".", 1)[0] == stem]
        pool = exact_stem or candidates
        if len(pool) == 1:
            return pool[0]
        return min(pool, key=lambda candidate: (candidate.count("\\"), len(candidate), candidate.lower()))

    # -- localisation -----------------------------------------------------
    @property
    def strings(self) -> dict:
        """unit id -> {Name, Ubertip, Hotkey, ...} from the localisation files."""
        if self._strings is None:
            merged: dict[str, dict[str, str]] = {}
            present = {name.lower() for name in self.names()}
            for internal in STRING_FILES:
                if internal.lower() not in present:
                    continue
                table = self.load(internal)
                for row in table.rows:
                    if not row:
                        continue
                    unit_id = row[0]
                    record = {table.columns[i]: (row[i] if i < len(row) else "")
                              for i in range(len(table.columns))}
                    record.pop("section", None)
                    merged.setdefault(unit_id.lower(), {}).update(record)
            self._strings = merged
        return self._strings

    def unit_name(self, unit_id: str) -> str:
        record = self.strings.get(unit_id.lower())
        return record.get("Name", "") if record else ""

    # -- joins ------------------------------------------------------------
    def unit_ids(self) -> list[str]:
        data = self.load(UNIT_TABLES["data"])
        return data.column("unitID")

    def units(self, localized: bool = True, limit: int = 0) -> Table:
        """Join the four unit spreadsheets into one table, optionally with localised names."""
        tables = {key: self.load(path) for key, path in UNIT_TABLES.items()}
        indexed: dict[str, dict[str, dict[str, str]]] = {}
        id_columns = {"balance": "unitBalanceID", "data": "unitID",
                      "weapons": "unitWeapID", "abilities": "unitAbilID"}
        for key, table in tables.items():
            index = {}
            id_column = id_columns[key]
            if id_column not in table.columns:
                continue
            position = table.index_of(id_column)
            for row in table.rows:
                if position >= len(row):
                    continue
                record = {table.columns[i]: (row[i] if i < len(row) else "")
                          for i in range(len(table.columns))}
                index[row[position].lower()] = record
            indexed[key] = index

        columns = [name for name, _source, _column in UNIT_COLUMNS]
        rows = []
        for unit_id in indexed.get("data", {}):
            row = []
            for name, source, column in UNIT_COLUMNS:
                if source is None:
                    row.append(self.unit_name(unit_id) if localized else "")
                else:
                    record = indexed.get(source, {}).get(unit_id, {})
                    row.append(record.get(column, ""))
            rows.append(row)
        return Table("units", columns, rows, "join", "Units\\*")

    def tables_summary(self, limit: int = 0) -> list:
        """[(name, kind, rows, columns)] for every table, cheaply (header rows only)."""
        out = []
        for internal in self.names():
            try:
                table = self.load(internal)
            except (FileNotFoundError, LookupError, UnicodeDecodeError):
                continue
            out.append((internal, table.kind, table.row_count, table.column_count))
        return out[:limit] if limit else out
