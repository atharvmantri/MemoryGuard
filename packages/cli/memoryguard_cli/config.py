# SPDX-License-Identifier: Apache-2.0
"""Local store configuration + discovery for the ``memoryguard`` CLI.

A MemoryGuard *project store* lives in a ``.memoryguard/`` directory:

* ``.memoryguard/config.json`` — the project config (project name + relative DB
  path + a small schema version).
* ``.memoryguard/store.db`` — the local SQLite store the engine is built over.

The CLI resolves the active store either from an explicit ``--store PATH`` (a
project directory) or by walking up from the current working directory until a
``.memoryguard/config.json`` is found (so commands work anywhere inside a
project tree).

This module is part of the Apache-2.0 OSS CLI and is standard-library only.

Requirements: 1.1, 1.2, 1.5, 10.1.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

__all__ = [
    "CONFIG_DIRNAME",
    "CONFIG_FILENAME",
    "DEFAULT_DB_FILENAME",
    "CONFIG_VERSION",
    "StoreConfig",
    "StoreInitError",
    "config_dir",
    "config_path",
    "store_exists",
    "init_store",
    "discover_config",
    "load_config",
]

#: Name of the per-project MemoryGuard config directory.
CONFIG_DIRNAME = ".memoryguard"
#: Name of the JSON config file inside :data:`CONFIG_DIRNAME`.
CONFIG_FILENAME = "config.json"
#: Default SQLite filename inside :data:`CONFIG_DIRNAME`.
DEFAULT_DB_FILENAME = "store.db"
#: Schema version of the config file (bumped if the format changes).
CONFIG_VERSION = 1


class StoreInitError(RuntimeError):
    """Raised when a local store cannot be created (e.g. an unwritable path)."""


@dataclass(frozen=True)
class StoreConfig:
    """A resolved project store configuration.

    Attributes:
        project_name: human-readable project name (defaults to the dir name).
        db_path: absolute path to the SQLite store file.
        config_file: absolute path to the ``config.json`` that produced this.
        root: absolute path to the project directory (parent of
            ``.memoryguard``).
    """

    project_name: str
    db_path: Path
    config_file: Path
    root: Path


def config_dir(project_path: Path) -> Path:
    """Return the ``.memoryguard`` directory for ``project_path``."""
    return project_path / CONFIG_DIRNAME


def config_path(project_path: Path) -> Path:
    """Return the ``.memoryguard/config.json`` path for ``project_path``."""
    return config_dir(project_path) / CONFIG_FILENAME


def store_exists(project_path: Path) -> bool:
    """Return ``True`` when ``project_path`` already holds a config file."""
    return config_path(project_path).is_file()


def _read_config_file(cfg_file: Path) -> StoreConfig:
    """Load and normalize a ``config.json`` into a :class:`StoreConfig`."""
    data = json.loads(cfg_file.read_text(encoding="utf-8"))
    cfg_dir = cfg_file.parent
    root = cfg_dir.parent

    raw_db = str(data.get("db_path") or DEFAULT_DB_FILENAME)
    db = Path(raw_db)
    if not db.is_absolute():
        # Stored relative to the .memoryguard directory for portability.
        db = (cfg_dir / db).resolve()

    project_name = str(data.get("project_name") or root.name or "memoryguard")
    return StoreConfig(
        project_name=project_name,
        db_path=db,
        config_file=cfg_file.resolve(),
        root=root.resolve(),
    )


def init_store(
    project_path: Path,
    *,
    project_name: Optional[str] = None,
) -> tuple[StoreConfig, bool]:
    """Create (or preserve) a local store under ``project_path``.

    Idempotent (Requirement 1.2): when a store already exists the existing
    config is loaded and returned unchanged with ``created == False``.

    On an unwritable path (Requirement 1.5) a descriptive :class:`StoreInitError`
    is raised and no partial store is left behind — any directory created during
    the attempt is removed.

    Returns:
        ``(config, created)`` where ``created`` is ``True`` when a new store was
        written and ``False`` when an existing store was preserved.
    """
    project_path = project_path.expanduser()
    cfg_file = config_path(project_path)

    # Idempotency: preserve an existing store.
    if cfg_file.is_file():
        return _read_config_file(cfg_file), False

    cfg_dir = config_dir(project_path)
    # Track whether we are responsible for the .memoryguard dir so we can roll
    # back cleanly on failure (no partial store).
    created_cfg_dir = not cfg_dir.exists()

    try:
        project_path.mkdir(parents=True, exist_ok=True)
        cfg_dir.mkdir(parents=True, exist_ok=True)

        name = project_name or project_path.resolve().name or "memoryguard"
        payload = {
            "version": CONFIG_VERSION,
            "project_name": name,
            "db_path": DEFAULT_DB_FILENAME,
        }
        cfg_file.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        # Roll back any partial state so no half-created store remains.
        if created_cfg_dir and cfg_dir.exists():
            shutil.rmtree(cfg_dir, ignore_errors=True)
        raise StoreInitError(
            f"could not create a MemoryGuard store at {project_path}: {exc.strerror or exc}"
        ) from exc

    return _read_config_file(cfg_file), True


def discover_config(start: Optional[Path] = None) -> Optional[StoreConfig]:
    """Search ``start`` (or cwd) and its ancestors for a project store.

    Walks upward until a ``.memoryguard/config.json`` is found. Returns the
    resolved :class:`StoreConfig`, or ``None`` when no store is found.
    """
    current = (start or Path.cwd()).expanduser().resolve()
    for candidate in [current, *current.parents]:
        cfg_file = config_path(candidate)
        if cfg_file.is_file():
            return _read_config_file(cfg_file)
    return None


def load_config(store: Optional[Path] = None) -> StoreConfig:
    """Resolve the active store config or raise a descriptive error.

    When ``store`` is provided it must point at a project directory containing a
    ``.memoryguard/config.json`` (or an ancestor of one). When omitted, discovery
    starts from the current working directory.
    """
    cfg = discover_config(store)
    if cfg is None:
        where = str(store) if store is not None else "the current directory"
        raise StoreInitError(
            f"no MemoryGuard store found at or above {where}. "
            "Run `memoryguard init [PATH]` first."
        )
    return cfg
