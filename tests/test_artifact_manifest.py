import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from nexus import artifact_manifest as subject


VERSION = "0.340.108"
SOURCE = "a" * 40


def _stat_sample(**changes):
    fields = {
        "st_dev": 1,
        "st_ino": 2,
        "st_size": 3,
        "st_mtime_ns": 4,
        "st_ctime_ns": 5,
        "st_birthtime_ns": 6,
        "st_nlink": 1,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


def test_windows_path_descriptor_ctime_mismatch_uses_birthtime(monkeypatch):
    """CPython exposes different Windows ctime semantics for stat and fstat."""
    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    path_sample = _stat_sample(st_ctime_ns=10)
    descriptor_sample = _stat_sample(st_ctime_ns=11)

    assert not subject._same_file(path_sample, descriptor_sample)
    assert subject._same_path_and_open_file(path_sample, descriptor_sample)


def test_windows_path_descriptor_missing_birthtime_falls_back_only_when_both_absent(
        monkeypatch):
    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    monkeypatch.setattr(subject, "_WINDOWS_BIRTHTIME_REQUIRED", False)
    without_birth = vars(_stat_sample()).copy()
    without_birth.pop("st_birthtime_ns")

    assert subject._same_path_and_open_file(
        SimpleNamespace(**without_birth), SimpleNamespace(**without_birth))
    assert not subject._same_path_and_open_file(
        SimpleNamespace(**without_birth),
        SimpleNamespace(**dict(without_birth, st_ctime_ns=9)))
    assert not subject._same_path_and_open_file(
        _stat_sample(), SimpleNamespace(**without_birth))


def test_modern_windows_path_descriptor_requires_birthtime(monkeypatch):
    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    monkeypatch.setattr(subject, "_WINDOWS_BIRTHTIME_REQUIRED", True)
    without_birth = vars(_stat_sample()).copy()
    without_birth.pop("st_birthtime_ns")

    assert not subject._same_path_and_open_file(
        SimpleNamespace(**without_birth), SimpleNamespace(**without_birth))


@pytest.mark.parametrize("invalid", (None, True, 6.0, "6"))
def test_windows_path_descriptor_rejects_invalid_birthtime(monkeypatch, invalid):
    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    assert not subject._same_path_and_open_file(
        _stat_sample(), _stat_sample(st_birthtime_ns=invalid))


@pytest.mark.parametrize("changes", (
    {"st_dev": 9},
    {"st_ino": 9},
    {"st_size": 9},
    {"st_mtime_ns": 9},
    {"st_birthtime_ns": 9},
    {"st_nlink": 2},
))
def test_windows_path_descriptor_comparison_preserves_integrity_fields(
        monkeypatch, changes):
    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    assert not subject._same_path_and_open_file(
        _stat_sample(), _stat_sample(**changes))


def test_windows_stat_fstat_ctime_mismatch_verifies_distribution(
        tmp_path, monkeypatch):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"reviewed")
    document = subject.manifest([artifact], VERSION, SOURCE)
    path_type = type(tmp_path)
    original_lstat = path_type.lstat
    original_stat = subject.os.stat
    original_fstat = subject.os.fstat
    created_by_identity = {}

    class WindowsStatView:
        def __init__(self, sample, *, descriptor):
            self._sample = sample
            identity = sample.st_dev, sample.st_ino
            created = getattr(sample, "st_birthtime_ns", None)
            if type(created) is not int:
                created = created_by_identity.setdefault(identity, sample.st_ctime_ns)
            else:
                created_by_identity.setdefault(identity, created)
            self.st_birthtime_ns = created
            self.st_ctime_ns = created + int(descriptor)

        def __getattr__(self, name):
            return getattr(self._sample, name)

    def path_lstat(path):
        return WindowsStatView(original_lstat(path), descriptor=False)

    def path_stat(*args, **kwargs):
        return WindowsStatView(original_stat(*args, **kwargs), descriptor=False)

    def descriptor_stat(descriptor):
        return WindowsStatView(original_fstat(descriptor), descriptor=True)

    monkeypatch.setattr(subject, "_WINDOWS_STAT_COMPAT", True)
    monkeypatch.setattr(path_type, "lstat", path_lstat)
    monkeypatch.setattr(subject.os, "stat", path_stat)
    monkeypatch.setattr(subject.os, "fstat", descriptor_stat)

    assert subject.verify(document, tmp_path)["artifact_integrity_ok"]


def test_directory_path_only_check_does_not_require_cross_source_metadata(
        tmp_path, monkeypatch):
    _, identity = subject._artifact_directory(tmp_path)

    def unexpected_cross_source_check(*_args):
        raise AssertionError("path-only check must not compare path and descriptor metadata")

    monkeypatch.setattr(subject, "_same_path_and_open_file",
                        unexpected_cross_source_check)
    subject._assert_artifact_directory(tmp_path, identity)


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


@pytest.mark.parametrize("force_path_fallback", (False, True))
def test_verify_rejects_distribution_directory_replacement_between_artifacts(
        tmp_path, monkeypatch, force_path_fallback):
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "a.exe").write_bytes(b"reviewed-a")
    (expected / "b.exe").write_bytes(b"reviewed-b")
    document = subject.manifest(
        [expected / "a.exe", expected / "b.exe"], VERSION, SOURCE)

    distribution = tmp_path / "distribution"
    distribution.mkdir()
    (distribution / "a.exe").write_bytes(b"reviewed-a")
    (distribution / "b.exe").write_bytes(b"unreviewed-b")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "a.exe").write_bytes(b"unreviewed-a")
    (replacement / "b.exe").write_bytes(b"reviewed-b")

    retired = tmp_path / "retired"
    original_sha256 = subject.hashlib.sha256
    digests = 0
    replacement_denied = False

    class ReplacingDigest:
        def __init__(self):
            self._digest = original_sha256()

        def update(self, block):
            self._digest.update(block)

        def hexdigest(self):
            nonlocal digests, replacement_denied
            digests += 1
            if digests == 1:
                try:
                    distribution.rename(retired)
                    replacement.rename(distribution)
                except PermissionError:
                    # Windows can deny the attempted directory swap because
                    # the verifier deliberately retains every leaf handle.
                    # That is already a safe rejection, so let verification
                    # reach its fixed directory/digest disagreement instead
                    # of leaking the fixture's platform-specific exception.
                    replacement_denied = True
            return self._digest.hexdigest()

    monkeypatch.setattr(subject.hashlib, "sha256", ReplacingDigest)
    if force_path_fallback:
        monkeypatch.setattr(subject.os, "supports_dir_fd", set())
    with pytest.raises(subject.ArtifactManifestError) as rejected:
        subject.verify(document, distribution)
    if replacement_denied:
        assert str(rejected.value) in {
            "ARTIFACT_DIRECTORY_CHANGED", "ARTIFACT_DIGEST_DISAGREEMENT"}
    else:
        assert str(rejected.value) == "ARTIFACT_DIRECTORY_CHANGED"


def test_verify_rejects_symlinked_distribution_directory(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    artifact = target / "a.exe"
    artifact.write_bytes(b"a")
    document = subject.manifest([artifact], VERSION, SOURCE)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    with pytest.raises(subject.ArtifactManifestError,
                       match="INVALID_ARTIFACT_DIRECTORY"):
        subject.verify(document, alias)


def test_verify_rejects_symlinked_distribution_parent(tmp_path):
    real_parent = tmp_path / "real-parent"
    distribution = real_parent / "distribution"
    distribution.mkdir(parents=True)
    artifact = distribution / "a.exe"
    artifact.write_bytes(b"a")
    document = subject.manifest([artifact], VERSION, SOURCE)
    alias_parent = tmp_path / "alias-parent"
    try:
        alias_parent.symlink_to(real_parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    with pytest.raises(subject.ArtifactManifestError,
                       match="INVALID_ARTIFACT_DIRECTORY"):
        subject.verify(document, alias_parent / "distribution")


def test_verify_rejects_leaf_generation_change_between_artifacts(
        tmp_path, monkeypatch):
    first = tmp_path / "a.exe"
    second = tmp_path / "b.exe"
    first.write_bytes(b"reviewed-a")
    second.write_bytes(b"reviewed-b")
    document = subject.manifest([first, second], VERSION, SOURCE)
    original_sha256 = subject.hashlib.sha256
    digests = 0

    class ReplacingDigest:
        def __init__(self):
            self._digest = original_sha256()

        def update(self, block):
            self._digest.update(block)

        def hexdigest(self):
            nonlocal digests
            digests += 1
            if digests == 1:
                first.write_bytes(b"unreviewed")
                second.write_bytes(b"reviewed-b")
            return self._digest.hexdigest()

    monkeypatch.setattr(subject.hashlib, "sha256", ReplacingDigest)
    with pytest.raises(subject.ArtifactManifestError,
                       match="ARTIFACT_CHANGED_DURING_READ"):
        subject.verify(document, tmp_path)


@pytest.mark.parametrize("deny_restoration", (False, True))
def test_path_fallback_retains_leaf_identity_across_transient_directory_swap(
        tmp_path, monkeypatch, deny_restoration):
    distribution = tmp_path / "distribution"
    distribution.mkdir()
    artifact = distribution / "a.exe"
    artifact.write_bytes(b"reviewed")
    document = subject.manifest([artifact], VERSION, SOURCE)
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "a.exe").write_bytes(b"reviewed")
    retired = tmp_path / "retired"
    original = subject._open_artifact_leaf
    original_rename = type(distribution).rename
    swapped = False
    restoration_denied = False

    def platform_rename(path, target):
        if (deny_restoration and path == distribution
                and target == replacement and swapped):
            raise PermissionError("SIMULATED_WINDOWS_PINNED_LEAF")
        return original_rename(path, target)

    def transient_swap(directory, name, descriptor):
        nonlocal swapped, restoration_denied
        if not swapped:
            swapped = True
            directory.rename(retired)
            replacement.rename(directory)
            try:
                return original(directory, name, descriptor)
            finally:
                try:
                    directory.rename(replacement)
                    retired.rename(directory)
                except PermissionError:
                    # Windows denies the restoration while the replacement
                    # leaf is deliberately pinned.  Leave the changed public
                    # directory in place so the verifier can reject it and
                    # close the retained handle normally.
                    restoration_denied = True
        return original(directory, name, descriptor)

    monkeypatch.setattr(subject.os, "supports_dir_fd", set())
    monkeypatch.setattr(type(distribution), "rename", platform_rename)
    monkeypatch.setattr(subject, "_open_artifact_leaf", transient_swap)
    with pytest.raises(subject.ArtifactManifestError,
                       match="(?:ARTIFACT_DIRECTORY_CHANGED|ARTIFACT_CHANGED_DURING_READ)") as rejected:
        subject.verify(document, distribution)
    if restoration_denied:
        assert str(rejected.value) == "ARTIFACT_DIRECTORY_CHANGED"


def test_verify_falls_back_when_stat_dir_fd_is_unsupported(tmp_path, monkeypatch):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"reviewed")
    document = subject.manifest([artifact], VERSION, SOURCE)
    monkeypatch.setattr(subject.os, "supports_dir_fd", {subject.os.open})
    assert subject.verify(document, tmp_path)["artifact_integrity_ok"]


def test_retained_leaf_descriptor_closes_when_fdopen_fails(tmp_path, monkeypatch):
    descriptor_root = "/proc/self/fd"
    if not os.path.isdir(descriptor_root):
        pytest.skip("descriptor inventory unavailable")
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"reviewed")
    document = subject.manifest([artifact], VERSION, SOURCE)
    before = len(os.listdir(descriptor_root))

    def fail_fdopen(*_args, **_kwargs):
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(subject.os, "fdopen", fail_fdopen)
    with pytest.raises(subject.ArtifactManifestError,
                       match="ARTIFACT_CHANGED_DURING_READ"):
        subject.verify(document, tmp_path)
    assert len(os.listdir(descriptor_root)) == before


def test_open_artifact_leaf_closes_descriptor_when_initial_fstat_fails(
        tmp_path, monkeypatch):
    descriptor_root = "/proc/self/fd"
    if not os.path.isdir(descriptor_root):
        pytest.skip("descriptor inventory unavailable")
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"reviewed")
    document = subject.manifest([artifact], VERSION, SOURCE)
    before = len(os.listdir(descriptor_root))
    monkeypatch.setattr(subject.os, "supports_dir_fd", set())

    def fail_fstat(_descriptor):
        raise OSError("simulated initial fstat failure")

    monkeypatch.setattr(subject, "_opened_leaf_identity", fail_fstat)
    with pytest.raises(subject.ArtifactManifestError,
                       match="INVALID_ARTIFACT_FILE"):
        subject.verify(document, tmp_path)
    assert len(os.listdir(descriptor_root)) == before


def test_windows_style_same_size_cross_leaf_rewrite_is_rejected(
        tmp_path, monkeypatch):
    expected = tmp_path / "expected"
    expected.mkdir()
    (expected / "a.exe").write_bytes(b"reviewed-a")
    (expected / "b.exe").write_bytes(b"reviewed-b")
    document = subject.manifest(
        [expected / "a.exe", expected / "b.exe"], VERSION, SOURCE)
    distribution = tmp_path / "distribution"
    distribution.mkdir()
    first = distribution / "a.exe"
    second = distribution / "b.exe"
    first.write_bytes(b"reviewed-a")
    second.write_bytes(b"unreviewed")

    def windows_identity(left, right):
        fields = ("st_dev", "st_ino", "st_size", "st_nlink")
        return all(getattr(left, field) == getattr(right, field)
                   for field in fields)

    original_sha256 = subject.hashlib.sha256
    digests = 0

    class ReplacingDigest:
        def __init__(self):
            self._digest = original_sha256()

        def update(self, block):
            self._digest.update(block)

        def hexdigest(self):
            nonlocal digests
            digests += 1
            if digests == 1:
                first.write_bytes(b"unreviewed")
                second.write_bytes(b"reviewed-b")
            return self._digest.hexdigest()

    monkeypatch.setattr(subject, "_same_file", windows_identity)
    monkeypatch.setattr(subject, "_same_path_and_open_file", windows_identity)
    monkeypatch.setattr(subject.hashlib, "sha256", ReplacingDigest)
    with pytest.raises(subject.ArtifactManifestError,
                       match="ARTIFACT_DIGEST_DISAGREEMENT"):
        subject.verify(document, distribution)


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


def test_path_replacement_after_descriptor_hash_is_rejected(tmp_path,monkeypatch):
    artifact=tmp_path/'a.exe';artifact.write_bytes(b'reviewed bytes')
    retired=tmp_path/'retired.exe';real_fstat=subject.os.fstat;calls=[]
    def replace_on_final_descriptor_check(descriptor):
        observed=real_fstat(descriptor);calls.append(1)
        if len(calls)==2:
            os.replace(artifact,retired);artifact.write_bytes(b'unreviewed replacement')
        return observed
    monkeypatch.setattr(subject.os,'fstat',replace_on_final_descriptor_check)
    with pytest.raises(subject.ArtifactManifestError,match='ARTIFACT_CHANGED_DURING_READ'):
        subject.file_entry(artifact)


def test_descriptor_error_after_validation_is_changed_during_read(tmp_path, monkeypatch):
    artifact = tmp_path / "a.exe"
    artifact.write_bytes(b"reviewed bytes")
    real_fstat = subject.os.fstat
    calls = 0

    def fail_final_descriptor_check(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated descriptor instability")
        return real_fstat(descriptor)

    monkeypatch.setattr(subject.os, "fstat", fail_final_descriptor_check)
    with pytest.raises(subject.ArtifactManifestError,
                       match="ARTIFACT_CHANGED_DURING_READ"):
        subject.file_entry(artifact)


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


def test_distribution_envelope_detects_binding_review_substitution(tmp_path):
    payload=tmp_path/'NexusOperative.exe'; payload.write_bytes(b'application')
    inner=tmp_path/'artifact-manifest.json'
    subprocess.run(_command()+['create','--version',VERSION,'--commit',SOURCE,
        '--output',str(inner),str(payload)],check=True,capture_output=True)
    review=tmp_path/'distribution-binding-review.json'
    review.write_text('{"passed":true}\n',encoding='utf-8')
    envelope=tmp_path/'distribution-envelope.json'
    subprocess.run(_command()+['create','--version',VERSION,'--commit',SOURCE,
        '--output',str(envelope),str(inner),str(review)],check=True,capture_output=True)
    inner_verify=_command()+['verify',str(inner),str(tmp_path)]
    envelope_verify=_command()+['verify',str(envelope),str(tmp_path)]
    assert subprocess.run(inner_verify,capture_output=True).returncode==0
    assert subprocess.run(envelope_verify,capture_output=True).returncode==0
    review.write_text('{"passed":false}\n',encoding='utf-8')
    assert subprocess.run(inner_verify,capture_output=True).returncode==0
    changed=subprocess.run(envelope_verify,capture_output=True)
    assert changed.returncode==1
    assert changed.stdout==b'ARTIFACT_MANIFEST_CHECK_FAILED\n'

def test_inner_manifest_must_be_reverified_after_envelope_creation(tmp_path):
    payload=tmp_path/'NexusOperative.exe';payload.write_bytes(b'application')
    inner=tmp_path/'artifact-manifest.json'
    subprocess.run(_command()+['create','--version',VERSION,'--commit',SOURCE,
        '--output',str(inner),str(payload)],check=True,capture_output=True)
    review=tmp_path/'distribution-binding-review.json';review.write_text('{"passed":true}\n')
    envelope=tmp_path/'distribution-envelope.json'
    subprocess.run(_command()+['create','--version',VERSION,'--commit',SOURCE,
        '--output',str(envelope),str(inner),str(review)],check=True,capture_output=True)
    payload.write_bytes(b'post-envelope-mutation')
    envelope_verify=subprocess.run(_command()+['verify',str(envelope),str(tmp_path)],capture_output=True)
    inner_verify=subprocess.run(_command()+['verify',str(inner),str(tmp_path)],capture_output=True)
    assert envelope_verify.returncode==0
    assert inner_verify.returncode==1 and inner_verify.stdout==b'ARTIFACT_MANIFEST_CHECK_FAILED\n'


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
