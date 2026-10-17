"""Command-button icons: which picture belongs to which unit, ability, upgrade or item.

The game stores icons as paths in the ``Art`` key of the ``*Func.txt`` tables
(``Units\\HumanUnitFunc.txt`` & co.), and the unit's model comes from
``Units\\unitUI.slk`` (``file`` = ``units\\human\\Footman\\Footman``, without the
extension). This module joins the two so a model can answer "what is my icon".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

__all__ = ["IconRecord", "IconIndex", "KINDS", "build_index", "disabled_path", "export"]

KINDS = ("unit", "ability", "upgrade", "item")

FUNC_FILES = {
    "unit": [r"Units\HumanUnitFunc.txt", r"Units\OrcUnitFunc.txt",
             r"Units\UndeadUnitFunc.txt", r"Units\NightElfUnitFunc.txt",
             r"Units\NeutralUnitFunc.txt", r"Units\CampaignUnitFunc.txt"],
    "ability": [r"Units\HumanAbilityFunc.txt", r"Units\OrcAbilityFunc.txt",
                r"Units\UndeadAbilityFunc.txt", r"Units\NightElfAbilityFunc.txt",
                r"Units\NeutralAbilityFunc.txt", r"Units\CampaignAbilityFunc.txt",
                r"Units\CommonAbilityFunc.txt", r"Units\ItemAbilityFunc.txt"],
    "upgrade": [r"Units\HumanUpgradeFunc.txt", r"Units\OrcUpgradeFunc.txt",
                r"Units\UndeadUpgradeFunc.txt", r"Units\NightElfUpgradeFunc.txt",
                r"Units\NeutralUpgradeFunc.txt", r"Units\UpgradeFunc.txt"],
    "item": [r"Units\ItemFunc.txt"],
}

STRINGS_FILES = [r"Units\HumanUnitStrings.txt", r"Units\OrcUnitStrings.txt",
                 r"Units\UndeadUnitStrings.txt", r"Units\NightElfUnitStrings.txt",
                 r"Units\NeutralUnitStrings.txt", r"Units\CampaignUnitStrings.txt",
                 r"Units\HumanAbilityStrings.txt", r"Units\OrcAbilityStrings.txt",
                 r"Units\UndeadAbilityStrings.txt", r"Units\NightElfAbilityStrings.txt",
                 r"Units\NeutralAbilityStrings.txt", r"Units\CampaignAbilityStrings.txt",
                 r"Units\CommonAbilityStrings.txt", r"Units\ItemAbilityStrings.txt",
                 r"Units\HumanUpgradeStrings.txt", r"Units\OrcUpgradeStrings.txt",
                 r"Units\UndeadUpgradeStrings.txt", r"Units\NightElfUpgradeStrings.txt",
                 r"Units\NeutralUpgradeStrings.txt", r"Units\ItemStrings.txt"]

DISABLED_DIR = r"ReplaceableTextures\CommandButtonsDisabled"


@dataclass
class IconRecord:
    kind: str
    id: str
    name: str
    icon: str                 # path inside the archives, as written in the data
    model: str = ""           # model path for units and items
    buttonpos: str = ""
    hotkey: str = ""
    disabled: str = ""        # the greyed-out variant, when the archives have it

    @property
    def icon_name(self) -> str:
        return self.icon.rsplit("\\", 1)[-1]

    def as_dict(self) -> dict:
        return {"kind": self.kind, "id": self.id, "name": self.name, "icon": self.icon,
                "model": self.model, "buttonpos": self.buttonpos,
                "hotkey": self.hotkey, "disabled": self.disabled}


def disabled_path(icon: str) -> str:
    """``...\\CommandButtons\\BTNFoo.blp`` -> ``...\\CommandButtonsDisabled\\DISBTNFoo.blp``."""
    if not icon:
        return ""
    name = icon.rsplit("\\", 1)[-1]
    if not name.lower().startswith("btn") and not name.lower().startswith("pasbtn"):
        name = "BTN" + name
    return f"{DISABLED_DIR}\\DIS{name}"


class IconIndex:
    def __init__(self, records: list[IconRecord], game):
        self.records = records
        self.game = game
        self._by_id = {record.id.lower(): record for record in records}
        self._by_model = {}
        for record in records:
            if record.model:
                self._by_model[record.model.replace("/", "\\").lower()] = record
        # unitUI.file has no extension, so keep a stem-keyed map as well
        self._by_model_stem = {}
        for key, record in self._by_model.items():
            self._by_model_stem[key.rsplit(".", 1)[0]] = record

    # -- lookups ----------------------------------------------------------
    def by_id(self, wanted: str) -> IconRecord | None:
        return self._by_id.get(wanted.strip().lower())

    def for_model(self, model_path: str) -> IconRecord | None:
        key = model_path.replace("/", "\\").lower()
        if key in self._by_model:
            return self._by_model[key]
        stem = key.rsplit(".", 1)[0]
        if stem in self._by_model_stem:
            return self._by_model_stem[stem]
        # unitUI stores "units\human\Footman\Footman"; models are "Units\Human\Footman\Footman.mdx"
        if stem in self._by_model:
            return self._by_model[stem]
        return None

    def by_kind(self, kind: str = "") -> list[IconRecord]:
        return [record for record in self.records if not kind or record.kind == kind]

    def resolved(self, record: IconRecord, resources) -> Path | None:
        """PNG path for a record, converting the BLP on demand."""
        del resources  # kept in the signature for symmetry with other lookups
        return None

    def stats(self) -> dict:
        result = {"total": len(self.records), "kinds": {}}
        for kind in KINDS:
            items = self.by_kind(kind)
            result["kinds"][kind] = {
                "records": len(items),
                "with_icon": sum(1 for item in items if item.icon),
                "icons": len({item.icon for item in items if item.icon}),
                "with_model": sum(1 for item in items if item.model),
                "missing_files": sum(1 for item in items
                                     if item.icon and self.game.read(item.icon) is None),
            }
        return result


def build_index(game, tables=None) -> IconIndex:
    """Join the func tables (icons), unitUI/ItemData (models) and strings (names)."""
    if tables is None:
        from .tables import TableDatabase
        tables = TableDatabase(game)

    strings: dict[str, dict[str, str]] = {}
    for internal in STRINGS_FILES:
        try:
            table = tables.load(internal)
        except (FileNotFoundError, LookupError, ValueError):
            continue
        for row in table.rows:
            section = row[0].strip()
            if not section:
                continue
            record = strings.setdefault(section.lower(), {})
            for column, value in zip(table.columns[1:], row[1:]):
                if value.strip():
                    record.setdefault(column.lower(), value.strip())

    # unit id -> model: unitUI.file has no extension
    models: dict[str, str] = {}
    try:
        ui = tables.load("unitUI")
        id_index = ui.index_of("unitUIID")
        file_index = ui.index_of("file")
        for row in ui.rows:
            unit_id = row[id_index].strip()
            path = row[file_index].strip()
            if unit_id and path:
                models[unit_id.lower()] = path.replace("/", "\\")
    except (FileNotFoundError, LookupError, ValueError):
        pass

    records: list[IconRecord] = []
    seen: set[tuple] = set()
    for kind, files in FUNC_FILES.items():
        for internal in files:
            try:
                table = tables.load(internal)
            except (FileNotFoundError, LookupError, ValueError):
                continue
            columns = {column.lower(): column for column in table.columns}
            if "art" not in columns:
                continue
            id_index = table.index_of("section")
            art_index = table.index_of(columns["art"])
            extra = {}
            for key in ("buttonpos", "hotkey", "researchart"):
                if key in columns:
                    extra[key] = table.index_of(columns[key])
            for row in table.rows:
                object_id = row[id_index].strip()
                icon = row[art_index].strip()
                if not object_id or not icon:
                    continue
                key = (kind, object_id.lower())
                if key in seen:
                    continue
                seen.add(key)
                info = strings.get(object_id.lower(), {})
                model = models.get(object_id.lower(), "")
                if kind == "item" and not model:
                    model = _item_model(tables, object_id)
                record = IconRecord(
                    kind=kind, id=object_id,
                    name=info.get("name", "").strip() or object_id,
                    icon=icon.replace("/", "\\"),
                    model=_with_extension(model),
                    buttonpos=(row[extra["buttonpos"]].strip() if "buttonpos" in extra else ""),
                    hotkey=(row[extra["hotkey"]].strip() if "hotkey" in extra else ""),
                )
                candidate = disabled_path(record.icon)
                if game.read(candidate) is not None:
                    record.disabled = candidate
                if game.read(record.icon) is None:
                    continue  # data points at an icon that is not in this install
                records.append(record)

    records.sort(key=lambda item: (KINDS.index(item.kind), item.id.lower()))
    return IconIndex(records, game)


def _with_extension(path: str) -> str:
    if not path:
        return ""
    lower = path.lower()
    if lower.endswith((".mdx", ".mdl")):
        return path
    return path + ".mdx"


def _item_model(tables, item_id: str) -> str:
    try:
        items = tables.load("ItemData")
    except (FileNotFoundError, LookupError, ValueError):
        return ""
    try:
        id_index = items.index_of("itemID")
        file_index = items.index_of("file")
    except KeyError:
        return ""
    for row in items.rows:
        if row[id_index].strip().lower() == item_id.lower():
            return row[file_index].strip()
    return ""


# --------------------------------------------------------------------------- export

def export(index: IconIndex, resources, out: Path, kinds: list[str] | None = None,
           limit: int = 0, progress=None) -> dict:
    """Write every icon as PNG (keeping the archive layout) plus an ``icons.json``."""
    out = Path(out)
    wanted = [kind for kind in (kinds or KINDS) if kind in KINDS]
    records = [record for record in index.records if record.kind in wanted]
    if limit:
        records = records[:limit]

    stats = {"written": 0, "bytes": 0, "failed": 0, "skipped": 0,
             "records": len(records), "icons": 0}
    written_paths: dict[str, str] = {}
    for position, record in enumerate(records, 1):
        for source in (record.icon, record.disabled):
            if not source or source in written_paths:
                continue
            image = resources.image(source)
            if image is None:
                stats["failed"] += 1
                continue
            target = out / Path(source.replace("\\", "/") + ".png")
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target)
            stats["written"] += 1
            stats["bytes"] += target.stat().st_size
            written_paths[source] = str(target.relative_to(out).as_posix())
        if progress is not None and (position % 100 == 0 or position == len(records)):
            progress(position, len(records), record.id)

    stats["icons"] = len(written_paths)
    payload = {"stats": stats, "records": [
        {**record.as_dict(),
         "icon_png": written_paths.get(record.icon, ""),
         "disabled_png": written_paths.get(record.disabled, "")}
        for record in records]}
    index_path = out / "icons.json"
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    stats["index"] = str(index_path)
    return stats
