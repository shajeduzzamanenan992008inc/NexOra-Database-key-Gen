"""Regression tests for the CI skipped-test allowlist."""
from __future__ import annotations

import io
from pathlib import Path

from scripts.report_pytest_skips import (
    SMS_AUTH_REASON,
    SMS_SUITE_REASON,
    is_expected_optional_sms_skip,
    validate_junit_report,
)


def test_exact_sms_auth_module_collection_skip_is_allowed():
    details = (
        "('/home/runner/work/repo/repo/tests/test_sms_auth.py', 12, "
        + repr(SMS_AUTH_REASON)
        + ")"
    )
    assert is_expected_optional_sms_skip("", "tests.test_sms_auth", "collection skipped", details)


def test_exact_optional_sms_test_skip_is_allowed():
    details = f"/home/runner/work/repo/repo/tests/test_sms_keys.py:17: {SMS_SUITE_REASON}"
    assert is_expected_optional_sms_skip(
        "tests.test_sms_keys",
        "test_generate_stores_only_a_digest_and_masked_hint",
        SMS_SUITE_REASON,
        details,
    )


def test_unrelated_module_level_skip_is_rejected():
    assert not is_expected_optional_sms_skip(
        "", "tests.test_database", "collection skipped", "optional dependency missing"
    )


def test_sms_skip_with_different_reason_is_rejected():
    assert not is_expected_optional_sms_skip(
        "tests.test_sms_keys",
        "test_generate_stores_only_a_digest_and_masked_hint",
        "optional dependency missing",
        "/repo/tests/test_sms_keys.py:17: optional dependency missing",
    )


def test_non_sms_skip_is_rejected_even_when_reason_mentions_sms():
    assert not is_expected_optional_sms_skip(
        "tests.test_guardian_x_integration",
        "test_status",
        SMS_SUITE_REASON,
        f"/repo/tests/test_guardian_x_integration.py:17: {SMS_SUITE_REASON}",
    )


def test_reporter_fails_closed_for_unknown_skip(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="tests.test_other" name="test_other">'
        '<skipped message="unexpected">optional package missing</skipped>'
        '</testcase></testsuite></testsuites>',
        encoding="utf-8",
    )
    output = io.StringIO()
    assert validate_junit_report(report, output) is False
    assert "Unexpected skips" in output.getvalue()


def test_reporter_accepts_only_documented_optional_sms_skip_records(tmp_path):
    report = tmp_path / "results.xml"
    details = f"/repo/tests/test_sms_frontend.py:8: {SMS_SUITE_REASON}"
    report.write_text(
        "<testsuites><testsuite>"
        f'<testcase classname="tests.test_sms_frontend" name="test_sms_login" >'
        f'<skipped message="{SMS_SUITE_REASON}">{details}</skipped>'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    assert validate_junit_report(report, io.StringIO()) is True
