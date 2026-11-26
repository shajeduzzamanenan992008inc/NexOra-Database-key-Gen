"""Fail-closed reporter for the narrowly optional SMS Reader test package."""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TextIO

SMS_TEST_FILES = {
    "tests.test_sms_frontend": "test_sms_frontend.py",
    "tests.test_sms_keys": "test_sms_keys.py",
    "tests.test_sms_reader": "test_sms_reader.py",
}
SMS_SUITE_REASON = (
    "optional SMS Reader console package sms_reader is not installed; "
    "the NexOra /api/v1/reader suite is covered by tests/test_reader_api_lifecycle.py"
)
SMS_AUTH_REASON = (
    "Skipped: optional SMS Reader console package sms_reader is not installed; "
    "NexOra Reader API coverage lives in tests/test_reader_api_lifecycle.py"
)


def is_expected_optional_sms_skip(
    classname: str, name: str, message: str, details: str
) -> bool:
    """Allow only the exact, documented optional SMS Reader collection/test skips."""
    normalized = details.replace("\\", "/")
    if classname == "" and name == "tests.test_sms_auth":
        return (
            message == "collection skipped"
            and re.search(r"""(?:^|/)tests/test_sms_auth\.py['"]?,\s*\d+,""", normalized) is not None
            and SMS_AUTH_REASON in normalized
        )

    expected_file = SMS_TEST_FILES.get(classname)
    if expected_file is None:
        return False
    if message != SMS_SUITE_REASON:
        return False
    return re.search(
        rf"(?:^|/)tests/{re.escape(expected_file)}:\d+: {re.escape(SMS_SUITE_REASON)}$",
        normalized,
    ) is not None


def validate_junit_report(path: Path, output: TextIO = sys.stdout) -> bool:
    """Print all skips and reject every skip outside the explicit allowlist."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        print(f"Unable to read pytest JUnit report {path}: {type(exc).__name__}: {exc}", file=output)
        return False

    skipped: list[tuple[str, str, str, str]] = []
    for case in root.iter("testcase"):
        node = case.find("skipped")
        if node is not None:
            skipped.append(
                (
                    case.get("classname", ""),
                    case.get("name", ""),
                    node.get("message", "skipped"),
                    node.text or "",
                )
            )

    print(f"Skipped tests: {len(skipped)}", file=output)
    unexpected = []
    for classname, name, message, details in skipped:
        print(f"  - {classname}::{name} — {message}", file=output)
        if not is_expected_optional_sms_skip(classname, name, message, details):
            unexpected.append((classname, name))

    if unexpected:
        print("Unexpected skips (only the explicitly documented optional SMS Reader skips are allowed):", file=output)
        for classname, name in unexpected:
            print(f"  - {classname}::{name}", file=output)
        return False

    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python scripts/report_pytest_skips.py <pytest-results.xml>", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.is_file():
        print(f"Required pytest JUnit report is missing: {path}", file=sys.stderr)
        return 2
    return 0 if validate_junit_report(path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
