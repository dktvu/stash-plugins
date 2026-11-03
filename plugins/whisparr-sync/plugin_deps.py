"""
Install PyPI packages for whisparr-sync without PythonDepManager.

PythonDepManager always requires ``git`` in PATH (even for PyPI-only deps), which
official Stash Docker images often lack. This module installs pinned wheels into
``plugins/py_dependencies/`` (same parent folder layout as CommunityScripts) using
``pip install --target`` only.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from typing import Tuple


def _deps_root() -> Path:
    """Directory shared under the Stash ``plugins`` tree for isolated installs."""
    plugin_dir = Path(__file__).resolve().parent
    root = plugin_dir.parent / "py_dependencies"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _parse_spec(spec: str) -> Tuple[str, str]:
    """
    Parse ``import_name:package==version`` or ``package==version``.

    Returns:
        Tuple of (module name to import, pip install argument).
    """
    if ":" in spec:
        import_name, pip_spec = spec.split(":", 1)
    else:
        pip_spec = spec
        import_name = pip_spec.split("==")[0].split(">=")[0]
    return import_name.strip(), pip_spec.strip()


def _folder_name(import_name: str, pip_spec: str) -> str:
    if "==" in pip_spec:
        _pkg, version = pip_spec.split("==", 1)
        return f"{import_name}_{version}"
    return import_name


def _prepend_path(folder: Path) -> None:
    folder_str = str(folder.resolve())
    if folder_str in sys.path:
        sys.path.remove(folder_str)
    sys.path.insert(0, folder_str)


def _can_import(import_name: str) -> bool:
    try:
        importlib.import_module(import_name)
        return True
    except ImportError:
        return False


def ensure_import(*specs: str) -> None:
    """
    Ensure packages are importable, installing from PyPI on first use.

    Args:
        specs: PyPI install strings, optionally ``alias:package==version``.

    Raises:
        subprocess.CalledProcessError: If ``pip install`` fails.
        ImportError: If the module is still missing after install.
    """
    if not specs:
        return

    base = _deps_root()
    for spec in specs:
        import_name, pip_spec = _parse_spec(spec)
        folder = base / _folder_name(import_name, pip_spec)
        if folder.is_dir():
            _prepend_path(folder)
        if _can_import(import_name):
            continue

        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-input",
                "--upgrade",
                "--quiet",
                f"--target={folder.resolve()}",
                pip_spec,
            ],
            check=True,
        )
        _prepend_path(folder)
        sys.modules.pop(import_name, None)
        importlib.invalidate_caches()
        importlib.import_module(import_name)
