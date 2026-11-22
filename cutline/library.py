"""Offline design library for saving, searching, and reusing user-owned designs."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Sequence
import zipfile

from . import config, editor_model


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    name: str
    category: str
    tags: tuple[str, ...]
    created: str
    modified: str
    shapes_count: int
    width: float
    height: float
    file_path: Path


def library_dir(directory: Path | None = None) -> Path:
    """Return the base directory for offline design storage, creating it if needed."""
    folder = directory if directory is not None else (config.CONFIG_DIR / "library")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _sanitize_name(name: str) -> str:
    cleaned = re.sub(r'[\x00-\x1f\\/:"*?<>|]', '_', name.strip())
    if (not cleaned or len(cleaned) > 100 or ".." in name
            or name.strip().startswith(".") or "/" in name or "\\" in name):
        raise ValueError("Design name must be between 1 and 100 characters and cannot contain path traversal.")
    return cleaned


def _slug(name: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', name.lower().strip()).strip('-')
    return slug[:40] or "design"


def save_to_library(
    name: str,
    project_or_shapes: editor_model.Project | Sequence[editor_model.Shape],
    category: str = "User Designs",
    tags: Sequence[str] = (),
    directory: Path | None = None,
    width: float = 279.4,
    height: float = 215.9,
) -> Path:
    """Save a project or collection of shapes into the offline library."""
    clean_name = _sanitize_name(name)
    clean_category = category.strip() or "User Designs"
    clean_tags = tuple(str(t).strip() for t in tags if str(t).strip())

    if isinstance(project_or_shapes, editor_model.Project):
        project = project_or_shapes
    else:
        project = editor_model.Project(width, height, tuple(project_or_shapes))

    folder = library_dir(directory)
    slug = _slug(clean_name)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{slug}_{timestamp}.json"
    target = folder / filename

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data = {
        "id": slug,
        "name": clean_name,
        "category": clean_category,
        "tags": list(clean_tags),
        "created": now_iso,
        "modified": now_iso,
        **editor_model.project_document(project),
    }

    content = json.dumps(data, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(dir=folder, delete=False, mode="w", encoding="utf-8") as f:
        tmp_path = Path(f.name)
        f.write(content)
    tmp_path.replace(target)
    return target


def list_entries(
    directory: Path | None = None,
    query: str = "",
    category: str = "",
) -> list[LibraryEntry]:
    """List and filter designs saved in the offline library."""
    folder = library_dir(directory)
    query_lower = query.lower().strip()
    category_lower = category.lower().strip()
    results = []

    for path in folder.glob("*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(doc, dict) or "objects" not in doc:
                continue
            name = str(doc.get("name", path.stem))
            cat = str(doc.get("category", "User Designs"))
            tags = tuple(str(t) for t in doc.get("tags", ()))
            if category_lower and cat.lower() != category_lower:
                continue
            if query_lower:
                matches_name = query_lower in name.lower()
                matches_cat = query_lower in cat.lower()
                matches_tags = any(query_lower in t.lower() for t in tags)
                if not (matches_name or matches_cat or matches_tags):
                    continue
            page = doc.get("page_mm", [279.4, 215.9])
            results.append(LibraryEntry(
                id=str(doc.get("id", path.stem)),
                name=name,
                category=cat,
                tags=tags,
                created=str(doc.get("created", "")),
                modified=str(doc.get("modified", "")),
                shapes_count=len(doc.get("objects", [])),
                width=float(page[0]),
                height=float(page[1]),
                file_path=path,
            ))
        except (OSError, ValueError, TypeError):
            continue

    return sorted(results, key=lambda e: (e.modified, e.name), reverse=True)


def load_entry(
    identifier_or_path: str | Path,
    directory: Path | None = None,
) -> editor_model.Project:
    """Load a design from the library as an editable Project."""
    folder = library_dir(directory)
    if isinstance(identifier_or_path, Path) and identifier_or_path.is_file():
        path = identifier_or_path.resolve()
    else:
        # Search by exact filename, slug or id
        str_id = str(identifier_or_path)
        candidate = folder / str_id
        if not candidate.is_file():
            candidate = folder / f"{str_id}.json"
        if not candidate.is_file():
            matches = list(folder.glob(f"{str_id}_*.json"))
            if matches:
                candidate = matches[0]
        if not candidate.is_file():
            # Check by name in all entries
            for entry in list_entries(directory):
                if entry.id == str_id or entry.name == str_id:
                    candidate = entry.file_path
                    break
        path = candidate.resolve()

    if not path.is_file():
        raise FileNotFoundError(f"Library design not found: {identifier_or_path}")

    # Guard against directory traversal outside the library
    try:
        path.relative_to(folder.resolve())
    except ValueError:
        raise ValueError("Invalid library path outside designated library directory.")

    return editor_model.load_project(path)


def delete_entry(
    identifier_or_path: str | Path,
    directory: Path | None = None,
) -> bool:
    """Delete a design entry from the offline library."""
    folder = library_dir(directory)
    if isinstance(identifier_or_path, Path) and identifier_or_path.is_file():
        path = identifier_or_path.resolve()
    else:
        try:
            entry = load_entry(identifier_or_path, directory)
            # Find the path corresponding to it
            str_id = str(identifier_or_path)
            candidate = folder / str_id
            if not candidate.is_file():
                candidate = folder / f"{str_id}.json"
            if not candidate.is_file():
                matches = list(folder.glob(f"{str_id}_*.json"))
                if matches:
                    candidate = matches[0]
            if not candidate.is_file():
                for e in list_entries(directory):
                    if e.id == str_id or e.name == str_id:
                        candidate = e.file_path
                        break
            path = candidate.resolve()
        except (FileNotFoundError, ValueError):
            return False

    try:
        path.relative_to(folder.resolve())
    except ValueError:
        raise ValueError("Cannot delete path outside library directory.")

    if path.is_file():
        path.unlink()
        return True
    return False


def export_library(
    archive_path: Path,
    directory: Path | None = None,
) -> Path:
    """Export all library designs into a zip archive."""
    folder = library_dir(directory)
    archive_path = Path(archive_path).resolve()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in folder.glob("*.json"):
            zf.write(path, arcname=path.name)
    return archive_path


def import_library(
    archive_path: Path,
    directory: Path | None = None,
) -> int:
    """Import design JSON files from a zip archive, validating each before writing."""
    folder = library_dir(directory)
    archive_path = Path(archive_path).resolve()
    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    imported_count = 0
    with zipfile.ZipFile(archive_path, "r") as zf:
        for name in zf.namelist():
            if name.endswith("/") or not name.endswith(".json"):
                continue
            # Defend against zip slip
            safe_name = Path(name).name
            if not safe_name or safe_name != name:
                continue
            content = zf.read(name)
            try:
                doc = json.loads(content)
                if not isinstance(doc, dict) or "objects" not in doc:
                    continue
                # Verify that load_project would accept it
                with tempfile.NamedTemporaryFile(dir=folder, delete=False, mode="wb") as tmp:
                    tmp_p = Path(tmp.name)
                    tmp.write(content)
                try:
                    editor_model.load_project(tmp_p)
                    target = folder / safe_name
                    tmp_p.replace(target)
                    imported_count += 1
                except Exception:
                    tmp_p.unlink(missing_ok=True)
            except (json.JSONDecodeError, ValueError):
                continue

    return imported_count
