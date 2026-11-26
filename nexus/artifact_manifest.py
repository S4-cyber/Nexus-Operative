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
_WINDOWS_STAT_COMPAT = os.name == "nt"
_WINDOWS_BIRTHTIME_REQUIRED = _WINDOWS_STAT_COMPAT and sys.version_info >= (3, 12)
_MISSING_STAT_FIELD = object()


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


def _same_path_and_open_file(path_sample, open_sample):
    """Compare path/fd samples without mixing Windows ctime semantics.

    CPython 3.12+ exposes creation time through path-based ``stat``/``lstat``
    ``st_ctime_ns`` while ``fstat`` may expose change time for the same NTFS
    file.  Keep ctime for same-source stability checks, but use birth time for
    this cross-source identity comparison when Windows supplies it.
    """
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_nlink")
    if not all(getattr(path_sample, field) == getattr(open_sample, field)
               for field in fields):
        return False
    if _WINDOWS_STAT_COMPAT:
        path_birth = getattr(path_sample, "st_birthtime_ns", _MISSING_STAT_FIELD)
        open_birth = getattr(open_sample, "st_birthtime_ns", _MISSING_STAT_FIELD)
        if path_birth is _MISSING_STAT_FIELD and open_birth is _MISSING_STAT_FIELD:
            # Before CPython 3.12 Windows did not expose birth time and path
            # stat/fstat shared the original change-time meaning.  Preserve
            # that strict comparison for supported older runtimes only.
            return (not _WINDOWS_BIRTHTIME_REQUIRED
                    and path_sample.st_ctime_ns == open_sample.st_ctime_ns)
        return (type(path_birth) is int and type(open_birth) is int
                and path_birth == open_birth)
    return path_sample.st_ctime_ns == open_sample.st_ctime_ns


def _artifact_directory(directory):
    """Return one canonical directory identity without following any alias."""
    path = Path(os.path.abspath(os.fspath(directory)))
    try:
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current /= component
            observed = current.lstat()
            if stat.S_ISLNK(observed.st_mode):
                _fail("INVALID_ARTIFACT_DIRECTORY")
        identity = path.lstat()
    except ArtifactManifestError:
        raise
    except OSError:
        _fail("INVALID_ARTIFACT_DIRECTORY")
    if not stat.S_ISDIR(identity.st_mode):
        _fail("INVALID_ARTIFACT_DIRECTORY")
    return path, identity


def _assert_artifact_directory(path, identity, descriptor=None):
    """Require the public directory and optional pinned handle to stay exact."""
    try:
        current = path.lstat()
        opened = os.fstat(descriptor) if descriptor is not None else identity
    except OSError:
        _fail("ARTIFACT_DIRECTORY_CHANGED")
    if (stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode)
            or not stat.S_ISDIR(opened.st_mode)
            or not _same_file(identity, current)
            or (descriptor is not None
                and not _same_path_and_open_file(identity, opened))):
        _fail("ARTIFACT_DIRECTORY_CHANGED")


def _leaf_stat(directory, name, descriptor):
    try:
        if descriptor is not None:
            return os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        return (directory / name).lstat()
    except (OSError, TypeError, NotImplementedError):
        _fail("INVALID_ARTIFACT_FILE")


def _opened_leaf_identity(descriptor):
    return os.fstat(descriptor)


def _open_artifact_leaf(directory, name, descriptor):
    """Open and retain one stable leaf before any set member is hashed."""
    path = directory / name
    if not _valid_name(name):
        _fail("INVALID_ARTIFACT_NAME")
    observed = _leaf_stat(directory, name, descriptor)
    if (stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or not 1 <= observed.st_size <= MAX_ARTIFACT_BYTES):
        _fail("INVALID_ARTIFACT_FILE")
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    leaf = None
    try:
        leaf = (os.open(name, flags, dir_fd=descriptor) if descriptor is not None
                else os.open(path, flags))
        opened = _opened_leaf_identity(leaf)
    except (OSError, TypeError, NotImplementedError):
        if leaf is not None:
            os.close(leaf)
        _fail("INVALID_ARTIFACT_FILE")
    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
            or not 1 <= opened.st_size <= MAX_ARTIFACT_BYTES
            or not _same_path_and_open_file(observed, opened)):
        os.close(leaf)
        _fail("INVALID_ARTIFACT_FILE")
    return leaf, opened


def _assert_artifact_leaf(directory, name, descriptor, leaf, identity):
    try:
        opened = os.fstat(leaf)
        public = _leaf_stat(directory, name, descriptor)
    except ArtifactManifestError:
        raise
    except OSError:
        _fail("ARTIFACT_CHANGED_DURING_READ")
    if (not stat.S_ISREG(public.st_mode) or public.st_nlink != 1
            or not _same_file(identity, opened)
            or not _same_path_and_open_file(public, identity)):
        _fail("ARTIFACT_CHANGED_DURING_READ")


def _hash_artifact_leaf(name, leaf, identity):
    """Hash one retained handle without releasing the set-wide identity pin."""
    digest = hashlib.sha256()
    total = 0
    duplicate = None
    try:
        duplicate = os.dup(leaf)
        os.lseek(duplicate, 0, os.SEEK_SET)
        try:
            stream = os.fdopen(duplicate, "rb")
        except Exception:
            os.close(duplicate)
            duplicate = None
            raise
        duplicate = None
        with stream:
            before = os.fstat(stream.fileno())
            if not _same_file(identity, before):
                _fail("ARTIFACT_CHANGED_DURING_READ")
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
    except (OSError, TypeError, ValueError, RuntimeError):
        _fail("ARTIFACT_CHANGED_DURING_READ")
    finally:
        if duplicate is not None:
            os.close(duplicate)
    if total != identity.st_size or not _same_file(identity, after):
        _fail("ARTIFACT_CHANGED_DURING_READ")
    return {"name": name, "bytes": total, "sha256": digest.hexdigest()}


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
    descriptor_validated = False
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or not 1 <= before.st_size <= MAX_ARTIFACT_BYTES
                    or (observed.st_dev, observed.st_ino) != (before.st_dev, before.st_ino)):
                _fail("INVALID_ARTIFACT_FILE")
            descriptor_validated = True
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
        _fail("ARTIFACT_CHANGED_DURING_READ" if descriptor_validated
              else "INVALID_ARTIFACT_FILE")
    try:
        final = path.lstat()
    except OSError:
        _fail("ARTIFACT_CHANGED_DURING_READ")
    if (total != before.st_size or not _same_file(before, after)
            or not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
            or not _same_path_and_open_file(final, after)):
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
    directory, directory_identity = _artifact_directory(directory)
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
             | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    descriptor = None
    leaves = []
    try:
        relative_supported = (hasattr(os, "O_DIRECTORY")
            and os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
            and os.stat in os.supports_follow_symlinks)
        if relative_supported:
            try:
                descriptor = os.open(directory, flags)
            except OSError:
                _fail("INVALID_ARTIFACT_DIRECTORY")
        _assert_artifact_directory(directory, directory_identity, descriptor)
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
        for entry in document["artifacts"]:
            _assert_artifact_directory(directory, directory_identity, descriptor)
            leaf, identity = _open_artifact_leaf(
                directory, entry["name"], descriptor)
            leaves.append((entry, leaf, identity))
            _assert_artifact_directory(directory, directory_identity, descriptor)
        for entry, leaf, identity in leaves:
            _assert_artifact_leaf(
                directory, entry["name"], descriptor, leaf, identity)
        first_pass = {}
        for pass_index in range(2):
            for entry, leaf, identity in leaves:
                _assert_artifact_directory(
                    directory, directory_identity, descriptor)
                _assert_artifact_leaf(
                    directory, entry["name"], descriptor, leaf, identity)
                actual = _hash_artifact_leaf(entry["name"], leaf, identity)
                _assert_artifact_directory(
                    directory, directory_identity, descriptor)
                _assert_artifact_leaf(
                    directory, entry["name"], descriptor, leaf, identity)
                if actual != entry:
                    _fail("ARTIFACT_DIGEST_DISAGREEMENT")
                if pass_index == 0:
                    first_pass[entry["name"]] = actual
                elif actual != first_pass[entry["name"]]:
                    _fail("ARTIFACT_CHANGED_DURING_READ")
        for entry, leaf, identity in leaves:
            _assert_artifact_leaf(
                directory, entry["name"], descriptor, leaf, identity)
        _assert_artifact_directory(directory, directory_identity, descriptor)
    finally:
        for _entry, leaf, _identity in leaves:
            os.close(leaf)
        if descriptor is not None:
            os.close(descriptor)
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
