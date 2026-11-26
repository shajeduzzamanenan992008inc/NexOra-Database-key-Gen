"""Portable regression contracts for NexOra's optional GUARDIAN-X integration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import guardian_x_status as gx


def _profile() -> dict:
    return json.loads(gx.PROFILE.read_text(encoding="utf-8"))


def test_host_profile_is_nexora_specific_and_declares_existing_protected_paths():
    profile = _profile()
    assert profile["schemaVersion"] == 1
    assert "NexOra" in profile["project"]["name"]
    assert profile["project"]["criticality"] == "high"
    assert profile["governance"]["securityBaseline"] == "strict"
    assert profile["ci"]["enabled"] is True
    assert profile["ci"]["noChecksPolicy"] == "fail"
    assert profile["ci"]["checks"]
    assert "CHANGE-ME" not in gx.PROFILE.read_text(encoding="utf-8")
    excluded = profile["scope"]["excludedProjects"]
    assert any("Android Reader App" in item["name"] for item in excluded)
    for value in profile["governance"]["protectedPaths"]:
        assert (gx.ROOT / value.rstrip("/")).exists(), value


def test_optional_runtime_probe_reports_public_checkout_without_runtime():
    runtime = gx._runtime_installation()
    assert runtime["status"] in {"not_installed", "installed"}
    assert runtime["available"] is (runtime["status"] == "installed")
    if runtime["status"] == "not_installed":
        assert runtime["missing"] == []
        assert runtime["unsafe"] == []




def test_symlinked_runtime_root_is_rejected(tmp_path, monkeypatch):
    class SymlinkRoot:
        def is_symlink(self):
            return True

        def __str__(self):
            return str(tmp_path / "outside-runtime")

    monkeypatch.setattr(gx, "SHIELD", SymlinkRoot())
    runtime = gx._runtime_installation()
    assert runtime["status"] == "unsafe"
    assert runtime["available"] is False
    assert runtime["unsafe"]


def test_status_does_not_import_runtime_when_it_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(gx, "SHIELD", tmp_path / "not-installed")
    monkeypatch.setattr(
        gx,
        "_prepare_core",
        lambda: (_ for _ in ()).throw(AssertionError("optional runtime must not be imported")),
    )
    status = gx.collect_status()
    assert status["guardian"]["status"] == "not_installed"
    assert status["guardian"]["available"] is False

def test_incomplete_runtime_is_not_treated_as_installed(tmp_path, monkeypatch):
    incomplete = tmp_path / "GUARDIAN-X"
    incomplete.mkdir()
    monkeypatch.setattr(gx, "SHIELD", incomplete)

    runtime = gx._runtime_installation()

    assert runtime["status"] == "incomplete"
    assert runtime["available"] is False
    assert set(runtime["missing"]) == {"entrypoint", "core_package", "policy"}


def test_link_manifest_hash_validation_uses_a_portable_fixture(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    shield = root / "GUARDIAN-X"
    managed = root / "docs" / "managed-by-guardian.txt"
    shield.mkdir(parents=True)
    managed.parent.mkdir(parents=True)
    content = "managed baseline\n"
    managed.write_text(content, encoding="utf-8")
    manifest = shield / "link-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "managed_by": "omega-shield-link",
                "project_root": str(root.resolve()),
                "groups": ["agents"],
                "managed_files": {
                    "docs/managed-by-guardian.txt": hashlib.sha256(content.encode()).hexdigest()
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gx, "ROOT", root)
    monkeypatch.setattr(gx, "SHIELD", shield)
    monkeypatch.setattr(gx, "MANIFEST", manifest)

    intact = gx._link_state()
    assert intact["integrated"] is True
    assert intact["managed_files"] == 1
    assert intact["integrity_ok"] is True
    assert intact["healthy"] is True
    assert intact["intact"] == ["docs/managed-by-guardian.txt"]

    managed.write_text(content + "user edit\n", encoding="utf-8")
    modified = gx._link_state()
    assert modified["integrity_ok"] is False
    assert modified["modified"] == ["docs/managed-by-guardian.txt"]
    assert managed.read_text(encoding="utf-8") == content + "user edit\n"


def test_link_status_does_not_claim_missing_manifest_is_integrated(tmp_path, monkeypatch):
    root = tmp_path / "checkout"
    shield = root / "GUARDIAN-X"
    shield.mkdir(parents=True)
    manifest = shield / "link-manifest.json"
    monkeypatch.setattr(gx, "ROOT", root)
    monkeypatch.setattr(gx, "SHIELD", shield)
    monkeypatch.setattr(gx, "MANIFEST", manifest)

    link = gx._link_state()

    assert link["present"] is False
    assert link["integrated"] is False
    assert link["integrity_ok"] is False
    assert link["healthy"] is False


def test_agent_instructions_keep_host_boundaries_authoritative():
    text = gx.AGENTS.read_text(encoding="utf-8") + gx.INSTRUCTION.read_text(encoding="utf-8")
    for term in ("Android Reader App", "production", "GUARDIAN-X", "Never commit", "TEST_DATABASE_URL"):
        assert term in text
    assert "CHANGE-ME" not in text


def test_status_contains_optional_connection_surface_without_initializing_missing_runtime():
    status = gx.collect_status()
    assert status["schema"] == "nexora.guardian_x_status.v1"
    for key in ("host", "guardian", "connection", "reports", "approvals", "capabilities"):
        assert key in status
    assert status["host"]["profile"]["valid"] is True
    assert status["guardian"]["status"] in {"not_installed", "installed", "incomplete", "unsafe", "error"}
    assert status["guardian"]["available"] is (status["guardian"]["status"] == "installed")
    if not status["guardian"]["available"]:
        assert status["guardian"]["version"] is None
        assert status["reports"]["available"] is False
        assert status["connection"]["registry"]["present"] is False


def test_verify_portable_checks_pass_without_private_or_local_runtime():
    status = gx.run_checks(require_local=False)
    failed = [item for item in status["verification"]["checks"] if item["status"] == "fail"]
    assert status["verification"]["passed"] is True, failed
    assert status["verification"]["summary"]["failed"] == 0


def test_verify_local_fails_closed_when_runtime_is_not_installed():
    if gx._runtime_installation()["status"] == "installed":
        return

    status = gx.run_checks(require_local=True)
    assert status["verification"]["passed"] is False
    runtime_check = next(
        item for item in status["verification"]["checks"] if item["id"] == "guardian-runtime"
    )
    assert runtime_check["status"] == "fail"
