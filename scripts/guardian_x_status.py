#!/usr/bin/env python3
"""Read-only status for NexOra's optional, separately provisioned GUARDIAN-X tool.

A public clone works without the private/local runtime. Portable verification checks
repository-owned policy; --verify --local additionally requires a complete runtime,
link manifest, matching checkout path, and external OGX registration.

Usage:
    python scripts/guardian_x_status.py
    python scripts/guardian_x_status.py --verify
    python scripts/guardian_x_status.py --verify --local
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import runpy
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SHIELD = ROOT / "GUARDIAN-X"
PROFILE = ROOT / ".github" / "project-profile.json"
AGENTS = ROOT / "AGENTS.md"
INSTRUCTION = ROOT / ".github" / "instructions" / "nexora-guardian.instructions.md"
MANIFEST = SHIELD / "link-manifest.json"
MAX_REPORT_BYTES = 8 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None, f"file exceeds {MAX_REPORT_BYTES} bytes"
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _safe_rel(value: str) -> str:
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe manifest path: {value!r}")
    return path.as_posix()


def _normal_path(value: str | Path) -> str:
    return os.path.normcase(os.path.realpath(str(Path(value).expanduser())))


def _state_home() -> Path:
    os.environ.setdefault("OMEGA_SHIELD_DIRNAME", SHIELD.name)
    os.environ.setdefault("OMEGA_HOST_ROOT", str(ROOT))
    os.environ.setdefault("OMEGA_SHIELD_HOME", str(SHIELD))
    namespace = runpy.run_path(str(SHIELD / "init.py"))
    return Path(namespace["state_home"]())


def _prepare_core() -> Path:
    state = _state_home()
    # Match the state directory selected by GUARDIAN-X/init.py, not the generic
    # OGX default. Reports and the project registry must remain outside the host.
    os.environ.setdefault("OGX_HOME", str(state))
    core = str(SHIELD / "core")
    if core not in sys.path:
        sys.path.insert(0, core)
    import ogx  # noqa: F401  (import is intentional after sys.path setup)

    loaded = Path(ogx.__file__).resolve()
    if not loaded.is_relative_to((SHIELD / "core" / "ogx").resolve()):
        raise RuntimeError(f"another ogx package shadows the shield runtime: {loaded}")
    return state


def _profile_state() -> dict[str, Any]:
    data, error = _read_json(PROFILE)
    state: dict[str, Any] = {
        "path": str(PROFILE),
        "present": PROFILE.is_file(),
        "sha256": _sha256(PROFILE) if PROFILE.is_file() else None,
        "valid": False,
        "name": None,
        "criticality": None,
        "security_baseline": None,
        "error": error,
    }
    if isinstance(data, dict):
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        governance = data.get("governance") if isinstance(data.get("governance"), dict) else {}
        state.update({
            "valid": data.get("schemaVersion") == 1,
            "name": project.get("name"),
            "criticality": project.get("criticality"),
            "security_baseline": governance.get("securityBaseline"),
            "protected_paths": governance.get("protectedPaths", []),
            "checks_configured": len(data.get("ci", {}).get("checks", [])) if isinstance(data.get("ci"), dict) else 0,
        })
    return state


def _link_state() -> dict[str, Any]:
    state: dict[str, Any] = {
        "manifest": str(MANIFEST),
        "present": MANIFEST.is_file(),
        "valid": False,
        "integrated": False,
        "groups": [],
        "managed_files": 0,
        "intact": [],
        "modified": [],
        "missing": [],
        "unsafe": [],
        "host_path_matches": False,
        "integrity_ok": False,
        "healthy": False,
        "error": None,
    }
    if SHIELD.is_symlink() or MANIFEST.is_symlink():
        state["unsafe"].append(str(MANIFEST))
        state["error"] = "refusing symlinked GUARDIAN-X link manifest path"
        return state
    if not MANIFEST.is_file():
        return state
    data, error = _read_json(MANIFEST)
    state["error"] = error
    if not isinstance(data, dict):
        return state
    if data.get("managed_by") != "omega-shield-link" or not isinstance(data.get("managed_files"), dict):
        state["error"] = "manifest is not a recognized GUARDIAN-X link manifest"
        return state
    state["valid"] = True
    state["integrated"] = True
    state["groups"] = data.get("groups", []) if isinstance(data.get("groups"), list) else []
    state["managed_files"] = len(data["managed_files"])
    manifest_root = str(data.get("project_root", ""))
    state["manifest_root"] = manifest_root
    state["host_path_matches"] = bool(manifest_root) and _normal_path(manifest_root) == _normal_path(ROOT)
    for rel, expected in data["managed_files"].items():
        try:
            safe_rel = _safe_rel(rel)
        except ValueError:
            state["unsafe"].append(str(rel))
            continue
        if not isinstance(expected, str) or len(expected) != 64:
            state["unsafe"].append(safe_rel)
            continue
        target = ROOT / safe_rel
        try:
            if target.is_symlink():
                state["unsafe"].append(safe_rel)
            elif not target.exists():
                state["missing"].append(safe_rel)
            elif not target.is_file():
                state["unsafe"].append(safe_rel)
            elif _sha256(target) == expected:
                state["intact"].append(safe_rel)
            else:
                state["modified"].append(safe_rel)
        except OSError:
            state["missing"].append(safe_rel)
    state["integrity_ok"] = not (state["modified"] or state["missing"] or state["unsafe"])
    state["healthy"] = bool(state["integrated"] and state["integrity_ok"] and state["host_path_matches"])
    return state


def _registry_state(state_home: Path) -> dict[str, Any]:
    path = state_home / "projects.json"
    result: dict[str, Any] = {
        "path": str(path),
        "present": path.is_file(),
        "registered": False,
        "name": None,
        "path_matches": False,
        "error": None,
    }
    if not path.is_file():
        return result
    data, error = _read_json(path)
    result["error"] = error
    if not isinstance(data, dict):
        return result
    matches = []
    for name, value in data.items():
        if isinstance(name, str) and isinstance(value, str):
            try:
                if _normal_path(value) == _normal_path(ROOT):
                    matches.append(name)
            except OSError:
                continue
    result["registered"] = bool(matches)
    result["name"] = sorted(matches)[0] if matches else None
    result["path_matches"] = bool(matches)
    return result


def _report_entry(path: Path) -> dict[str, Any]:
    entry: dict[str, Any] = {"path": str(path), "modified_at": None, "generated_at": None}
    try:
        entry["modified_at"] = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        pass
    data, _ = _read_json(path)
    if isinstance(data, dict):
        entry["generated_at"] = data.get("generated_at")
        entry["engine_version"] = data.get("engine_version") or data.get("version")
    return entry


def _latest_report(report_dir: Path, prefixes: tuple[str, ...]) -> dict[str, Any] | None:
    if not report_dir.is_dir():
        return None
    candidates = []
    for path in report_dir.glob("*.json"):
        if path.is_file() and path.name.startswith(prefixes):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
    if not candidates:
        return None
    return _report_entry(max(candidates, key=lambda item: item[0])[1])


def _report_summary(report_dir: Path) -> dict[str, Any]:
    latest_audit = _latest_report(report_dir, ("guardian-audit-",))
    summary: dict[str, Any] = {
        "directory": str(report_dir),
        "last_scan": _latest_report(report_dir, ("scan-", "analyze-")),
        "last_audit": latest_audit,
        "last_maintenance": _latest_report(report_dir, ("guardian-maintain-",)),
        "last_evolution": _latest_report(report_dir, ("guardian-evolve-",)),
        "finding_counts": None,
        "recommendations": None,
        "quality_gates": None,
    }
    if latest_audit:
        data, _ = _read_json(Path(latest_audit["path"]))
        if isinstance(data, dict):
            health = data.get("health") if isinstance(data.get("health"), dict) else {}
            summary["finding_counts"] = health.get("finding_counts")
            if isinstance(data.get("recommendations"), list):
                summary["recommendations"] = len(data["recommendations"])
            summary["quality_gates"] = data.get("quality_gates")
    return summary


def _approval_state(state_home: Path) -> dict[str, Any]:
    path = state_home / "enterprise" / "ogx-enterprise.sqlite3"
    result: dict[str, Any] = {"path": str(path), "available": False, "pending": 0, "statuses": {}}
    if not path.is_file() or path.is_symlink():
        return result
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=2)
        try:
            rows = connection.execute(
                "SELECT status, COUNT(*) FROM approval_requests GROUP BY status"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    result["available"] = True
    result["statuses"] = {str(status): int(count) for status, count in rows}
    result["pending"] = int(result["statuses"].get("pending", 0) + result["statuses"].get("sending", 0))
    return result


def _policy_state() -> dict[str, Any]:
    policy_path = SHIELD / "shield.toml"
    safe_path = not SHIELD.is_symlink() and not policy_path.is_symlink()
    state: dict[str, Any] = {
        "path": str(policy_path),
        "present": safe_path and policy_path.is_file(),
        "restricted": False,
        "default_mode": None,
        "allow_file_writes": None,
        "allow_network": None,
        "allow_execution": None,
        "approval_required": None,
        "automatic_commit_push_deploy": None,
        "error": None,
    }
    if not safe_path:
        state["error"] = "refusing symlinked GUARDIAN-X policy path"
        return state
    if not state["present"]:
        return state
    try:
        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
        return state
    security = data.get("security", {}) if isinstance(data.get("security"), dict) else {}
    governance = data.get("governance", {}) if isinstance(data.get("governance"), dict) else {}
    state.update({
        "allow_file_writes": security.get("allow_file_writes"),
        "allow_network": security.get("allow_network"),
        "allow_execution": security.get("allow_execution"),
        "default_mode": governance.get("default_mode"),
        "approval_required": governance.get("require_approval_for_changes"),
        "automatic_commit_push_deploy": governance.get("allow_automatic_commit_push_deploy"),
    })
    state["restricted"] = all(
        state[key] is False for key in ("allow_file_writes", "allow_network", "allow_execution")
    )
    return state


def _backend_version() -> str | None:
    path = ROOT / "backend" / "__init__.py"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except (OSError, UnicodeError):
        return None
    return None


def _runtime_installation() -> dict[str, Any]:
    """Describe the optional drop-in runtime without executing or modifying it."""
    state: dict[str, Any] = {
        "status": "not_installed",
        "available": False,
        "path": str(SHIELD),
        "missing": [],
        "unsafe": [],
        "error": None,
    }
    if SHIELD.is_symlink():
        state.update({"status": "unsafe", "unsafe": [str(SHIELD)]})
        return state
    if not SHIELD.exists():
        return state
    if not SHIELD.is_dir():
        state.update({"status": "unsafe", "unsafe": [str(SHIELD)]})
        return state

    required = {
        "entrypoint": SHIELD / "init.py",
        "core_package": SHIELD / "core" / "ogx" / "__init__.py",
        "policy": SHIELD / "shield.toml",
    }
    for name, path in required.items():
        parents = [SHIELD]
        current = SHIELD
        for part in path.relative_to(SHIELD).parts[:-1]:
            current = current / part
            parents.append(current)
        if any(parent.is_symlink() for parent in parents) or path.is_symlink():
            state["unsafe"].append(name)
        elif not path.is_file():
            state["missing"].append(name)

    if state["unsafe"]:
        state["status"] = "unsafe"
    elif state["missing"]:
        state["status"] = "incomplete"
    else:
        state["status"] = "installed"
        state["available"] = True
    return state


def _empty_registry_state() -> dict[str, Any]:
    return {
        "path": None,
        "present": False,
        "registered": False,
        "name": None,
        "path_matches": False,
        "error": None,
    }


def _empty_report_summary(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "directory": None,
        "last_scan": None,
        "last_audit": None,
        "last_maintenance": None,
        "last_evolution": None,
        "finding_counts": None,
        "recommendations": None,
        "quality_gates": None,
    }


def _empty_approval_state() -> dict[str, Any]:
    return {"path": None, "available": False, "pending": 0, "statuses": {}}


def collect_status() -> dict[str, Any]:
    installation = _runtime_installation()
    runtime_version: str | None = None
    state_home: Path | None = None
    registry = _empty_registry_state()
    reports = _empty_report_summary(
        "The optional GUARDIAN-X runtime is not installed in this checkout."
    )
    approvals = _empty_approval_state()
    guardian_error = installation.get("error")

    if installation["status"] == "installed":
        try:
            state_home = _prepare_core()
            import ogx
            from ogx.cli.common import _external_report_dir

            runtime_version = ogx.__version__
            reports = _report_summary(_external_report_dir(ROOT))
            reports["available"] = True
            reports["reason"] = None
            registry = _registry_state(state_home)
            approvals = _approval_state(state_home)
        except Exception as exc:
            # Never expose exception payloads from optional external runtime code.
            installation["status"] = "error"
            installation["available"] = False
            guardian_error = f"{type(exc).__name__}: optional runtime initialization failed"
            installation["error"] = guardian_error
            state_home = None
            reports = _empty_report_summary(guardian_error)
            registry = _empty_registry_state()
            approvals = _empty_approval_state()

    policy = _policy_state()
    available = installation["status"] == "installed"
    return {
        "schema": "nexora.guardian_x_status.v1",
        "generated_at": _now(),
        "host": {
            "name": "NexOra Database Key Generator",
            "root": str(ROOT),
            "backend_version": _backend_version(),
            "profile": _profile_state(),
        },
        "guardian": {
            "status": installation["status"],
            "available": available,
            "path": str(SHIELD),
            "missing": installation.get("missing", []),
            "unsafe": installation.get("unsafe", []),
            "error": guardian_error,
            "version": runtime_version,
            "state_home": str(state_home) if state_home else None,
            "policy": policy,
        },
        "connection": {
            "registry": registry,
            "link": _link_state(),
        },
        "reports": reports,
        "approvals": approvals,
        "capabilities": {
            "analysis": (
                "optional local GUARDIAN-X analysis runtime available"
                if available else "unavailable until the optional GUARDIAN-X runtime is installed"
            ),
            "tests": reports.get("quality_gates", {}).get("tests") if reports.get("quality_gates") else "not_executed",
            "build": "not_executed_by_guardian",
            "runtime": "not_executed_by_guardian",
            "per_path_write_blocking": "not_implemented",
            "link_integrity": "sha256_manifest" if available else "not_configured",
            "safe_removal": "unchanged_managed_files_only" if available else "not_available",
        },
    }


def _check(checks: list[dict[str, Any]], check_id: str, ok: bool, detail: str) -> None:
    checks.append({"id": check_id, "status": "pass" if ok else "fail", "detail": detail})


def _not_required(checks: list[dict[str, Any]], check_id: str, detail: str) -> None:
    checks.append({"id": check_id, "status": "not_required", "detail": detail})


def run_checks(*, require_local: bool = False) -> dict[str, Any]:
    status = collect_status()
    checks: list[dict[str, Any]] = []
    profile = status["host"]["profile"]
    link = status["connection"]["link"]
    registry = status["connection"]["registry"]
    policy = status["guardian"]["policy"]
    guardian_status = status["guardian"]["status"]

    _check(checks, "profile-present", bool(profile["present"]), profile["path"])
    _check(checks, "profile-valid", bool(profile["valid"]), "schemaVersion 1")
    _check(
        checks,
        "profile-identity",
        "nexora" in str(profile.get("name", "")).casefold()
        and profile.get("criticality") == "high"
        and profile.get("security_baseline") == "strict",
        f"name={profile.get('name')!r}, criticality={profile.get('criticality')!r}, "
        f"baseline={profile.get('security_baseline')!r}",
    )
    data, profile_error = _read_json(PROFILE)
    protected = []
    if isinstance(data, dict):
        governance = data.get("governance") if isinstance(data.get("governance"), dict) else {}
        protected = governance.get("protectedPaths", []) if isinstance(governance.get("protectedPaths"), list) else []
    missing_paths = []
    for value in protected:
        if not isinstance(value, str):
            missing_paths.append(repr(value))
            continue
        target = ROOT / value.rstrip("/")
        if not target.exists():
            missing_paths.append(value)
    _check(
        checks,
        "protected-paths-exist",
        bool(protected) and not missing_paths and profile_error is None,
        f"{len(protected)} declared; missing={missing_paths}",
    )
    _check(checks, "agent-instructions", AGENTS.is_file(), str(AGENTS))
    _check(checks, "copilot-instructions", INSTRUCTION.is_file(), str(INSTRUCTION))
    agent_text = ""
    for path in (AGENTS, INSTRUCTION):
        try:
            agent_text += path.read_text(encoding="utf-8")
        except OSError:
            pass
    _check(
        checks,
        "agent-boundary-rules",
        all(term in agent_text for term in ("Android Reader App", "production", "GUARDIAN-X", "commit")),
        "scope, production, Guardian-X, and commit boundaries present",
    )

    if guardian_status == "installed":
        _check(checks, "guardian-runtime", True, status["guardian"]["path"])
        _check(
            checks,
            "shield-policy-restricted",
            bool(policy["restricted"]),
            json.dumps({
                key: policy[key] for key in ("allow_file_writes", "allow_network", "allow_execution")
            }),
        )
        if link["present"]:
            _check(
                checks,
                "link-manifest",
                bool(link["valid"] and link["integrated"] and link["integrity_ok"] and link["managed_files"] > 0),
                f"managed={link['managed_files']}, modified={len(link['modified'])}, "
                f"missing={len(link['missing'])}, unsafe={len(link['unsafe'])}",
            )
        elif require_local:
            _check(checks, "link-manifest", False, f"not configured: {link['manifest']}")
        else:
            _not_required(checks, "link-manifest", "Optional Guardian-X is installed but not linked to this checkout.")
    elif guardian_status == "not_installed" and not require_local:
        _not_required(
            checks,
            "guardian-runtime",
            "GUARDIAN-X is an optional, separately provisioned tool and is not bundled in public NexOra clones.",
        )
        _not_required(checks, "shield-policy-restricted", "No local GUARDIAN-X shield policy is installed to inspect.")
        _not_required(checks, "link-manifest", "No local GUARDIAN-X link manifest is configured.")
    else:
        _check(
            checks,
            "guardian-runtime",
            False,
            f"GUARDIAN-X installation status is {guardian_status!r}; expected a complete, safe installation.",
        )
        _not_required(checks, "shield-policy-restricted", "Runtime policy cannot be evaluated without a valid installation.")
        _check(checks, "link-manifest", False, "A valid GUARDIAN-X runtime and link manifest are required.")

    if require_local:
        _check(checks, "local-host-path", bool(link["host_path_matches"]), link.get("manifest_root", ""))
        _check(
            checks,
            "local-registration",
            bool(registry["registered"] and registry["path_matches"]),
            f"registry={registry.get('path')!r}, name={registry.get('name')!r}",
        )
    status["verification"] = {
        "required_local": require_local,
        "checks": checks,
        "passed": not any(item["status"] == "fail" for item in checks),
        "summary": {
            "passed": sum(item["status"] == "pass" for item in checks),
            "not_required": sum(item["status"] == "not_required" for item in checks),
            "failed": sum(item["status"] == "fail" for item in checks),
        },
    }
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only status for the NexOra <-> GUARDIAN-X host integration")
    parser.add_argument("--verify", action="store_true", help="Run read-only integration checks")
    parser.add_argument("--local", action="store_true", help="With --verify, also require this checkout's path and registry entry")
    parser.add_argument("--compact", action="store_true", help="Print compact JSON")
    args = parser.parse_args(argv)
    if args.local and not args.verify:
        parser.error("--local requires --verify")
    try:
        status = run_checks(require_local=args.local) if args.verify else collect_status()
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"schema": "nexora.guardian_x_status.v1", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 2
    print(json.dumps(status, indent=None if args.compact else 2, ensure_ascii=False))
    if args.verify and not status["verification"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
