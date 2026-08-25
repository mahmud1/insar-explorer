#!/usr/bin/env python3
"""Build and validate the QGIS Plugin Repository release archive.

The package is created directly from the checked-out repository. Development
content is copied to a temporary staging directory only when it is part of the
release policy; the source tree is never modified.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional


PLUGIN_ROOT = "insar_explorer-dev"
ARCHIVE_NAME = f"{PLUGIN_ROOT}.zip"

EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".ipynb_checkpoints",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".vscode",
    "__pycache__",
    "build",
    "cover",
    "dist",
    "help",
    "htmlcov",
    "scripts",
    "test",
    "tests",
    "tools",
}

EXCLUDED_FILE_NAMES = {
    ".DS_Store",
    ".coverage",
    ".gitignore",
    ".readthedocs.yaml",
    "Makefile",
    "coverage.xml",
    "nosetests.xml",
    "pb_tool.cfg",
    "pylintrc",
}

EXCLUDED_SUFFIXES = {
    ".bak",
    ".cover",
    ".log",
    ".pyc",
    ".pyo",
    ".swp",
    ".tmp",
}

REQUIRED_PATHS = (
    "__init__.py",
    "metadata.txt",
    "icon.png",
    "insar_explorer.py",
    "insar_explorer_dockwidget.py",
    "insar_explorer_dockwidget_base.ui",
    "resources.py",
    "src",
    "external",
    "external/pyqtgraph",
    "external_licenses",
    "external_licenses/pyqtgraph_license.txt",
    "LICENSE",
)

FORBIDDEN_PATH_PARTS = {
    ".git",
    ".github",
    ".pytest_cache",
    "__pycache__",
    "build",
    "dist",
    "help",
    "scripts",
    "test",
    "tests",
    "tools",
}

FORBIDDEN_FILE_NAMES = {".DS_Store"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo"}

# A fixed ZIP timestamp makes repeated builds byte-stable when source contents
# and the Python/zlib implementation are unchanged.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def repository_root() -> Path:
    """Return the repository root containing this packaging tool."""
    return Path(__file__).resolve().parents[1]


def should_exclude(path: Path) -> bool:
    """Return whether a repository-relative path is development-only."""
    if any(part in EXCLUDED_DIR_NAMES for part in path.parts[:-1]):
        return True
    if path.name in EXCLUDED_DIR_NAMES:
        return True
    if path.name in EXCLUDED_FILE_NAMES:
        return True
    if path.name.startswith(".coverage.") or path.name.startswith("._"):
        return True
    if path.suffix in EXCLUDED_SUFFIXES:
        return True
    return False


def copy_release_tree(repo_root: Path, stage_root: Path) -> None:
    """Copy release-relevant repository content into *stage_root*."""
    stage_root.mkdir(parents=True, exist_ok=False)

    for source in sorted(repo_root.rglob("*"), key=lambda p: p.as_posix()):
        relative = source.relative_to(repo_root)
        if should_exclude(relative):
            continue

        target = stage_root / relative
        if source.is_symlink():
            raise RuntimeError(f"Refusing to package unsupported symlink: {relative}")
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _missing_required_paths(stage_root: Path) -> List[str]:
    return [path for path in REQUIRED_PATHS if not (stage_root / path).exists()]


def _forbidden_paths(stage_root: Path) -> list[str]:
    violations: List[str] = []
    for path in stage_root.rglob("*"):
        relative = path.relative_to(stage_root)
        if any(part in FORBIDDEN_PATH_PARTS for part in relative.parts):
            violations.append(relative.as_posix())
        elif path.name in FORBIDDEN_FILE_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            violations.append(relative.as_posix())
    return sorted(set(violations))


def validate_staged_package(stage_root: Path) -> None:
    """Fail when required release files are absent or forbidden files leaked in."""
    missing = _missing_required_paths(stage_root)
    if missing:
        raise RuntimeError("Staged package is missing required paths: " + ", ".join(missing))

    forbidden = _forbidden_paths(stage_root)
    if forbidden:
        raise RuntimeError(
            "Staged package contains forbidden development content: "
            + ", ".join(forbidden)
        )


def iter_release_files(stage_root: Path) -> Iterable[Path]:
    """Yield staged files in deterministic archive order."""
    return sorted(
        (path for path in stage_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(stage_root).as_posix(),
    )


def create_archive(stage_root: Path, archive_path: Path) -> int:
    """Write a deterministic ZIP archive and return the staged file count."""
    files = list(iter_release_files(stage_root))
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.unlink(missing_ok=True)

    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for source in files:
            relative = source.relative_to(stage_root).as_posix()
            info = zipfile.ZipInfo(f"{PLUGIN_ROOT}/{relative}", ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes(), compresslevel=9)

    return len(files)


def validate_archive(archive_path: Path) -> None:
    """Validate the final ZIP root, required paths, and exclusions."""
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()

    if not names:
        raise RuntimeError("Created QGIS package is empty")

    roots = {name.split("/", 1)[0] for name in names}
    if roots != {PLUGIN_ROOT}:
        raise RuntimeError(
            f"Archive must have the single root {PLUGIN_ROOT!r}; found {sorted(roots)!r}"
        )

    missing: List[str] = []
    for required in REQUIRED_PATHS:
        expected = f"{PLUGIN_ROOT}/{required}"
        if expected in names:
            continue
        directory_prefix = f"{expected.rstrip('/')}/"
        if not any(name.startswith(directory_prefix) for name in names):
            missing.append(expected)
    if missing:
        raise RuntimeError("Archive is missing required content: " + ", ".join(missing))

    violations: List[str] = []
    for name in names:
        relative_parts = Path(name).parts[1:]
        if any(part in FORBIDDEN_PATH_PARTS for part in relative_parts):
            violations.append(name)
        elif Path(name).name in FORBIDDEN_FILE_NAMES:
            violations.append(name)
        elif Path(name).suffix in FORBIDDEN_SUFFIXES:
            violations.append(name)

    if violations:
        raise RuntimeError(
            "Archive contains forbidden development content: "
            + ", ".join(sorted(set(violations)))
        )


def build_package(repo_root: Optional[Path] = None) -> Path:
    """Build, validate, and return ``dist/insar_explorer-dev.zip``."""
    root = (repo_root or repository_root()).resolve()
    if not (root / "metadata.txt").is_file() or not (root / "__init__.py").is_file():
        raise RuntimeError(f"Not an InSAR Explorer repository root: {root}")

    output = root / "dist" / ARCHIVE_NAME

    with tempfile.TemporaryDirectory(prefix="insar_explorer_qgis_") as temp_dir:
        stage_root = Path(temp_dir) / PLUGIN_ROOT
        copy_release_tree(root, stage_root)
        validate_staged_package(stage_root)
        file_count = create_archive(stage_root, output)
        validate_archive(output)

    size_kib = output.stat().st_size / 1024
    print(f"QGIS package created: dist/{ARCHIVE_NAME}")
    print(f"Staged files: {file_count}; archive size: {size_kib:.1f} KiB")
    print("Reminder: run 'make security' successfully before repository upload.")
    return output


def main() -> None:
    """Command-line entry point."""
    build_package()


if __name__ == "__main__":
    main()
