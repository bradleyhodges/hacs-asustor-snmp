#!/usr/bin/env python3
"""Install into an existing HA configuration, preserving an upgrade backup.

This utility needs Python 3.10+ and the standard library only. It never restarts
Home Assistant, installs dependencies, changes configuration.yaml, or deletes a
previous integration backup. Run with permission to write the configuration dir.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DOMAIN = "asustor_snmp"


def file_hashes(directory: Path) -> dict[str, str]:
    """Verify every installed file, excluding interpreter-generated caches."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def install(config_dir: Path, source: Path) -> Path | None:
    """Stage and verify before replacing the integration; restore on failure."""
    config_dir = config_dir.resolve(strict=True)
    if not any((config_dir / name).is_file() for name in ("configuration.yaml", ".HA_VERSION")):
        raise ValueError("The selected directory does not look like an HA configuration directory")
    if json.loads((source / "manifest.json").read_text())["domain"] != DOMAIN:
        raise ValueError("The source manifest has an unexpected integration domain")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("Refusing an installation source containing symbolic links")
    parent = config_dir / "custom_components"
    if parent.is_symlink():
        raise ValueError("Resolve the custom_components symlink manually before installation")
    parent.mkdir(exist_ok=True)
    target = parent / DOMAIN
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError("The integration target must be a directory, not a link or file")
    lock = parent / f".{DOMAIN}-install.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    backup = None
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=f".{DOMAIN}-stage-", dir=parent))
        staged_component = stage / DOMAIN
        shutil.copytree(
            source, staged_component, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        if file_hashes(staged_component) != file_hashes(source):
            raise OSError("The staged integration failed integrity verification")
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = config_dir / "asustor_snmp_backups" / stamp / DOMAIN
            backup.parent.mkdir(parents=True)
            target.rename(backup)
        try:
            staged_component.rename(target)
        except BaseException:
            if backup is not None:
                backup.rename(target)
            raise
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)
        lock.unlink(missing_ok=True)
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir", required=True, type=Path, help="Host directory mounted at /config"
    )
    args = parser.parse_args()
    source = Path(__file__).resolve().parent / "custom_components" / DOMAIN
    try:
        backup = install(args.config_dir, source)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Installation failed: {exc}\n")
    destination = args.config_dir.resolve() / "custom_components" / DOMAIN
    print(f"Installed ASUSTOR NAS (SNMP) in {destination}")
    if backup:
        print(f"Previous version preserved at: {backup}")
    print("Restart Home Assistant, then add ASUSTOR NAS (SNMP) under Devices & services.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
