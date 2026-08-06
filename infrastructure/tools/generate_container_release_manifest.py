"""Generate a deterministic, non-secret manifest of approved pilot image inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
APPROVED_INPUTS = (
    "Dockerfile.aws-pilot",
    "Dockerfile.aws-pilot-alpine-experimental",
    "requirements.txt",
    "app",
    "config/counties/schema.json",
    "config/counties/logan-synthetic.json",
    "database",
    "static",
    "templates",
)
FORBIDDEN_SEGMENTS = {
    ".git",
    ".github",
    ".venv",
    "__pycache__",
    "backups",
    "backup",
    "credentials",
    "docs",
    "scripts",
    "tests",
}
FORBIDDEN_FILE_PATTERN = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|.*\.(?:key|pem|p12|pfx)|credentials(?:\..*)?|secrets?(?:\..*)?)$",
    re.IGNORECASE,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_forbidden(relative_path: str) -> bool:
    parts = {part.lower() for part in Path(relative_path).parts}
    return bool(parts & FORBIDDEN_SEGMENTS) or bool(
        FORBIDDEN_FILE_PATTERN.search(relative_path.replace("\\", "/"))
    )


def _approved_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative in APPROVED_INPUTS:
        candidate = root / relative
        if not candidate.exists():
            raise FileNotFoundError(f"required container input is missing: {relative}")
        candidates: Iterable[Path]
        if candidate.is_dir():
            candidates = candidate.rglob("*")
        else:
            candidates = (candidate,)
        for path in candidates:
            relative_path = path.relative_to(root).as_posix()
            if path.is_symlink() or _is_forbidden(relative_path):
                continue
            if path.is_file():
                files.append(path)
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def generate_manifest(root: Path = REPOSITORY_ROOT) -> dict:
    root = root.resolve()
    entries = []
    aggregate = hashlib.sha256()
    for path in _approved_files(root):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        digest = _sha256(path)
        entries.append({"path": relative, "bytes": size, "sha256": digest})
        aggregate.update(f"{relative}\0{size}\0{digest}\n".encode("utf-8"))
    if not entries:
        raise ValueError("approved container input set is empty")
    return {
        "schema_version": "lcdash.phase1-container-release.v1",
        "status": "SOURCE_REVIEW_ONLY_NOT_BUILT",
        "hash_algorithm": "sha256",
        "source_manifest_sha256": aggregate.hexdigest(),
        "ecr_image_digest": None,
        "approved_inputs": list(APPROVED_INPUTS),
        "files": entries,
    }


def create_source_archive(destination: Path, root: Path = REPOSITORY_ROOT) -> dict:
    """Create a deterministic ZIP containing only approved manifest inputs."""
    root = root.resolve()
    manifest = generate_manifest(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for entry in manifest["files"]:
            path = root / entry["path"]
            info = zipfile.ZipInfo(entry["path"], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Emit the deterministic Phase 1 container source manifest."
    )
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    parser.add_argument(
        "--archive",
        type=Path,
        help="write an approved-input-only deterministic ZIP archive",
    )
    args = parser.parse_args(argv)
    manifest = (
        create_source_archive(args.archive) if args.archive else generate_manifest()
    )
    print(
        json.dumps(
            manifest,
            sort_keys=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
