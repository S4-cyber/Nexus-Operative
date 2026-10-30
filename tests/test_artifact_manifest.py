import json
import os
import subprocess
import sys

import pytest

from nexus import artifact_manifest as subject


VERSION = "0.340.64"
SOURCE = "a" * 40


def test_manifest_deterministic_and_detects_changed_distribution(tmp_path):
    application = tmp_path / "Nexus.exe"
    installer = tmp_path / "Setup.exe"
    application.write_bytes(b"packaged application")
    installer.write_bytes(b"installer")
    result = subject.manifest([installer, application], VERSION, SOURCE)
    assert result == subject.manifest([application, installer], VERSION, SOURCE)
    assert subject.verify(result, tmp_path)["artifact_integrity_ok"]
    assert not result["signed"] and not result["release_approved"]
    installer.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="DIGEST_DISAGREEMENT"):
        subject.verify(result, tmp_path)


@pytest.mark.parametrize("name", ("../private", "..", "", "/absolute", "folder/file", "folder\\file",
                                  "C:private", "nul\x00name", "line\nbreak"))
def test_manifest_rejects_paths(tmp_path, name):
    document = {
        "schema": subject.SCHEMA,
        "version": VERSION,
        "source_commit": SOURCE,
        "signed": False,
        "release_approved": False,
        "artifacts": [{"name": name, "bytes": 1, "sha256": "a" * 64}],
    }
    with pytest.raises(ValueError, match="INVALID_ARTIFACT_ENTRY"):
        subject.verify(document, tmp_path)


def test_duplicate_names_and_count_are_rejected(tmp_path):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"a")
    with pytest.raises(ValueError, match="UNIQUE"):
        subject.manifest([artifact, artifact], VERSION, SOURCE)
    document = subject.manifest([artifact], VERSION, SOURCE)
    document["artifacts"] *= 2
    with pytest.raises(ValueError, match="INVALID_ARTIFACT_ENTRY"):
        subject.verify(document, tmp_path)
    with pytest.raises(ValueError, match="COUNT"):
        subject.manifest([], VERSION, SOURCE)


@pytest.mark.parametrize("version,commit", (
    ("1.0.0", SOURCE),
    ("0.340.048", SOURCE),
    (VERSION, "A" * 40),
    (VERSION, "a" * 39),
))
def test_create_rejects_promoted_or_malformed_identity(tmp_path, version, commit):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"a")
    with pytest.raises(subject.ArtifactManifestError):
        subject.manifest([artifact], version, commit)


@pytest.mark.parametrize("mutation", (
    lambda value: value.update(extra=True),
    lambda value: value.update(signed=True),
    lambda value: value.update(release_approved=True),
    lambda value: value.update(version="1.0.0"),
    lambda value: value.update(source_commit="b" * 39),
    lambda value: value["artifacts"][0].update(extra=True),
    lambda value: value["artifacts"][0].update(bytes=True),
    lambda value: value["artifacts"][0].update(sha256="A" * 64),
))
def test_verify_requires_exact_shapes_and_false_authority(tmp_path, mutation):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"a")
    document = subject.manifest([artifact], VERSION, SOURCE)
    mutation(document)
    with pytest.raises(subject.ArtifactManifestError):
        subject.verify(document, tmp_path)


def test_symlink_and_hardlink_artifacts_are_rejected(tmp_path):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"a")
    linked = tmp_path / "linked.exe"
    os.link(artifact, linked)
    with pytest.raises(ValueError, match="INVALID_ARTIFACT_FILE"):
        subject.file_entry(artifact)
    linked.unlink()
    if hasattr(os, "symlink"):
        alias = tmp_path / "alias.exe"
        try:
            alias.symlink_to(artifact)
        except OSError:
            pytest.skip("symlinks unavailable")
        with pytest.raises(ValueError, match="INVALID_ARTIFACT_FILE"):
            subject.file_entry(alias)


def _command():
    return [sys.executable, "-m", "nexus.artifact_manifest"]


def test_cli_create_verify_missing_file_and_refuse_overwrite(tmp_path):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"a")
    output = tmp_path / "manifest.json"
    create = _command() + ["create", "--version", VERSION, "--commit", SOURCE,
                           "--output", str(output), str(artifact)]
    subprocess.run(create, check=True, capture_output=True)
    assert json.loads(output.read_text())["source_commit"] == SOURCE
    assert subprocess.run(create, capture_output=True).returncode == 1
    verify = _command() + ["verify", str(output), str(tmp_path)]
    verified = subprocess.run(verify, check=True, capture_output=True)
    assert verified.stdout == (b'{"artifact_integrity_ok":true,'
                               b'"release_approved":false,"signed":false}\n')
    assert verified.stderr == b""
    artifact.unlink()
    assert subprocess.run(verify, capture_output=True).returncode == 1


@pytest.mark.parametrize("raw", (
    b'{"schema":"nexus.artifacts.v1","schema":"nexus.artifacts.v1"}',
    b'{"schema":NaN}',
    b'{"schema":1.5}',
))
def test_cli_rejects_duplicate_keys_and_noninteger_numbers(tmp_path, raw):
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    result = subprocess.run(_command() + ["verify", str(path), str(tmp_path)], capture_output=True)
    assert result.returncode == 1
    assert result.stdout == b"ARTIFACT_MANIFEST_CHECK_FAILED\n"
    assert result.stderr == b""


def test_cli_rejects_hardlinked_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{}")
    alias = tmp_path / "manifest-copy.json"
    os.link(path, alias)
    result = subprocess.run(_command() + ["verify", str(path), str(tmp_path)], capture_output=True)
    assert result.returncode == 1
    assert result.stdout == b"ARTIFACT_MANIFEST_CHECK_FAILED\n"


def test_cli_usage_error_is_private_and_lf_only():
    result = subprocess.run(_command(), capture_output=True)
    assert result.returncode == 2
    assert result.stdout == b""
    assert result.stderr == b"ARTIFACT_MANIFEST_USAGE_ERROR\n"
