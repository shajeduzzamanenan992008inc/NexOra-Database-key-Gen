"""Host-integration contracts for the NexOra <-> GUARDIAN-X connection.

These tests validate only repository files and temporary copies. They never remove
or modify the real OGX-managed files and never touch a production database.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from scripts import guardian_x_status as gx


def _profile() -> dict:
    return json.loads(gx.PROFILE.read_text(encoding="utf-8"))


def test_host_profile_is_nexora_specific_and_paths_exist():
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


def test_link_manifest_hashes_are_intact():
    status = gx.collect_status()
    link = status["connection"]["link"]
    assert link["present"], f"missing link manifest: {link['manifest']}"
    assert link["integrated"] is True
    assert link["managed_files"] > 0
    assert link["integrity_ok"] is True, {
        "modified": link["modified"],
        "missing": link["missing"],
        "unsafe": link["unsafe"],
    }


def test_agent_instructions_keep_host_boundaries_authoritative():
    text = gx.AGENTS.read_text(encoding="utf-8") + gx.INSTRUCTION.read_text(encoding="utf-8")
    for term in ("Android Reader App", "production", "GUARDIAN-X", "Never commit", "TEST_DATABASE_URL"):
        assert term in text
    assert "CHANGE-ME" not in text


def test_status_contains_the_connection_surface():
    status = gx.collect_status()
    assert status["schema"] == "nexora.guardian_x_status.v1"
    for key in ("host", "guardian", "connection", "reports", "approvals", "capabilities"):
        assert key in status
    assert status["guardian"]["version"]
    assert status["guardian"]["policy"]["restricted"] is True
    assert status["host"]["profile"]["valid"] is True
    registry = status["connection"]["registry"]
    # The registry lives in external OGX state, so CI may not have it. If it is
    # present, it must either register this checkout or report a clean absence.
    if registry["present"]:
        assert registry["registered"] is True or registry["error"] is None


def test_remove_only_touches_unchanged_managed_files(tmp_path, monkeypatch):
    manifest_path = gx.MANIFEST
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    managed = manifest["managed_files"]
    assert managed

    temp_root = tmp_path / "checkout"
    temp_shield = temp_root / "GUARDIAN-X"
    temp_shield.mkdir(parents=True)
    for rel in managed:
        source = gx.ROOT / rel
        destination = temp_root / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    modified_rel = sorted(managed)[0]
    modified = temp_root / modified_rel
    modified.write_text(modified.read_text(encoding="utf-8") + "\nuser customization\n", encoding="utf-8")
    portable_manifest = dict(manifest)
    portable_manifest["project_root"] = str(temp_root.resolve())
    (temp_shield / "link-manifest.json").write_text(
        json.dumps(portable_manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    gx._prepare_core()
    from ogx.ecosystem.integration import remove_integration

    monkeypatch.setenv("OMEGA_SHIELD_DIRNAME", "GUARDIAN-X")
    result = remove_integration(temp_root)
    assert modified_rel in result["preserved_modified_or_missing"]
    assert modified.exists()
    assert modified_rel not in result["removed"]
    assert (temp_shield / "link-manifest.json").is_file()


def test_verify_portable_checks_pass():
    # Local path and registry checks are intentionally exercised by the operator
    # command (`--verify --local`), not by CI, because external OGX state is not
    # part of a portable checkout.
    status = gx.run_checks(require_local=False)
    failed = [item for item in status["verification"]["checks"] if item["status"] != "pass"]
    assert status["verification"]["passed"] is True, failed
