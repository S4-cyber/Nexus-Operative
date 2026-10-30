"""Strict checksums for reviewed distribution files; no signing or release authority."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


SCHEMA = "nexus.artifacts.v1"
MAX_ARTIFACTS = 16
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
VERSION = re.compile(r"0\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
MANIFEST_KEYS = {"schema", "version", "source_commit", "signed", "release_approved", "artifacts"}
ENTRY_KEYS = {"name", "bytes", "sha256"}


class ArtifactManifestError(ValueError):
    pass


def _fail(code):
    raise ArtifactManifestError(code)


def _valid_name(name):
    if (type(name) is not str or name in ("", ".", "..")
            or any(char in name for char in "/\\:")
            or any(ord(char) < 32 for char in name)):
        return False
    try:
        return len(name.encode("utf-8")) <= 255
    except UnicodeError:
        return False


def _same_file(before, after):
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
    return all(getattr(before, field) == getattr(after, field) for field in fields)


def file_entry(path):
    """Hash one stable regular single-link file through its open descriptor."""
    path = Path(path)
    if not _valid_name(path.name):
        _fail("INVALID_ARTIFACT_NAME")
    try:
        observed = path.lstat()
    except OSError:
        _fail("INVALID_ARTIFACT_FILE")
    if (path.is_symlink() or not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1
            or not 1 <= observed.st_size <= MAX_ARTIFACT_BYTES):
        _fail("INVALID_ARTIFACT_FILE")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 1 <= before.st_size <= MAX_ARTIFACT_BYTES
                    or (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino)):
                _fail("INVALID_ARTIFACT_FILE")
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARTIFACT_BYTES:
                    _fail("ARTIFACT_BYTE_CAP")
                digest.update(block)
            after = os.fstat(stream.fileno())
    except ArtifactManifestError:
        raise
    except OSError:
        _fail("INVALID_ARTIFACT_FILE")
    if total != before.st_size or not _same_file(before, after):
        _fail("ARTIFACT_CHANGED_DURING_READ")
    return {"name": path.name, "bytes": total, "sha256": digest.hexdigest()}


def _validate_header(version, commit):
    if type(version) is not str or VERSION.fullmatch(version) is None:
        _fail("INVALID_PRERELEASE_VERSION")
    if type(commit) is not str or SOURCE_COMMIT.fullmatch(commit) is None:
        _fail("INVALID_SOURCE_COMMIT")


def manifest(paths, version, commit):
    _validate_header(version, commit)
    paths = tuple(paths)
    if not 1 <= len(paths) <= MAX_ARTIFACTS:
        _fail("INVALID_ARTIFACT_COUNT")
    entries = sorted((file_entry(path) for path in paths), key=lambda entry: entry["name"])
    if len({entry["name"] for entry in entries}) != len(entries):
        _fail("ARTIFACT_NAMES_MUST_BE_NONEMPTY_AND_UNIQUE")
    return {
        "schema": SCHEMA,
        "version": version,
        "source_commit": commit,
        "signed": False,
        "release_approved": False,
        "artifacts": entries,
    }


def verify(document, directory):
    if type(document) is not dict or set(document) != MANIFEST_KEYS:
        _fail("INVALID_ARTIFACT_MANIFEST")
    _validate_header(document["version"], document["source_commit"])
    if (document["schema"] != SCHEMA or document["signed"] is not False
            or document["release_approved"] is not False
            or type(document["artifacts"]) is not list
            or not 1 <= len(document["artifacts"]) <= MAX_ARTIFACTS):
        _fail("INVALID_ARTIFACT_MANIFEST")
    seen = set()
    for entry in document["artifacts"]:
        if (type(entry) is not dict or set(entry) != ENTRY_KEYS
                or not _valid_name(entry.get("name")) or entry["name"] in seen
                or type(entry.get("bytes")) is not int
                or not 1 <= entry["bytes"] <= MAX_ARTIFACT_BYTES
                or type(entry.get("sha256")) is not str
                or SHA256.fullmatch(entry["sha256"]) is None):
            _fail("INVALID_ARTIFACT_ENTRY")
        seen.add(entry["name"])
        if file_entry(Path(directory) / entry["name"]) != entry:
            _fail("ARTIFACT_DIGEST_DISAGREEMENT")
    return {
        "artifact_integrity_ok": True,
        "signed": False,
        "release_approved": False,
    }


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("DUPLICATE_MANIFEST_KEY")
        result[key] = value
    return result


def _read_manifest(path):
    path = Path(path)
    if path.is_symlink():
        _fail("INVALID_MANIFEST_FILE")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 1 <= before.st_size <= MAX_MANIFEST_BYTES):
                _fail("INVALID_MANIFEST_FILE")
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(stream.fileno())
    except ArtifactManifestError:
        raise
    except OSError:
        _fail("INVALID_MANIFEST_FILE")
    if len(raw) != before.st_size or not _same_file(before, after):
        _fail("MANIFEST_CHANGED_DURING_READ")
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_pairs,
            parse_float=lambda _: _fail("INVALID_MANIFEST_NUMBER"),
            parse_constant=lambda _: _fail("INVALID_MANIFEST_NUMBER"),
        )
    except ArtifactManifestError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        _fail("INVALID_MANIFEST_JSON")


def _write_manifest(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        _fail("MANIFEST_OUTPUT_EXISTS")
    encoded = (json.dumps(document, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(encoded) > MAX_MANIFEST_BYTES:
        _fail("MANIFEST_BYTE_CAP")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".artifact-manifest-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            _fail("MANIFEST_OUTPUT_EXISTS")
        temporary.unlink()
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _write_line(sys.stderr, "ARTIFACT_MANIFEST_USAGE_ERROR")
        self.exit(2)


def _write_line(stream, value):
    """Write one UTF-8/LF record without platform newline translation."""
    encoded = value.encode("utf-8") + b"\n"
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(encoded.decode("utf-8"))
        stream.flush()
        return
    buffer.write(encoded)
    buffer.flush()


def main():
    parser = _Parser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--version", required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("files", type=Path, nargs="+")
    check = sub.add_parser("verify")
    check.add_argument("manifest", type=Path)
    check.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = manifest(args.files, args.version, args.commit)
            _write_manifest(args.output, result)
        else:
            result = verify(_read_manifest(args.manifest), args.directory)
            _write_line(sys.stdout, json.dumps(
                result, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ))
    except (ArtifactManifestError, MemoryError, OSError, RecursionError):
        _write_line(sys.stdout, "ARTIFACT_MANIFEST_CHECK_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
