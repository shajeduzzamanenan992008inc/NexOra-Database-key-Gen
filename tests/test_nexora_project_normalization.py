"""NexOra Phase 1 — Project Name Normalization and Duplicate Detection Tests."""

from __future__ import annotations

import pytest
from backend.security.keys import normalize_project_name


class TestProjectNameNormalization:
    def test_lowercase_conversion(self):
        assert normalize_project_name("NexOra Main") == "nexora main"
        assert normalize_project_name("NEXORA MAIN") == "nexora main"
        assert normalize_project_name("nexora main") == "nexora main"

    def test_whitespace_trimming(self):
        assert normalize_project_name("  NexOra Main  ") == "nexora main"
        assert normalize_project_name("\tNexOra Main\n") == "nexora main"

    def test_internal_whitespace_collapsing(self):
        assert normalize_project_name("NexOra   Database   Key") == "nexora database key"
        assert normalize_project_name("NexOra\t\tMain") == "nexora main"

    def test_case_insensitive_duplicate_match(self):
        """'NexOra Main' and 'nexora main' must normalize to the exact same string."""
        name1 = "NexOra Main"
        name2 = "nexora main"
        name3 = "  NEXORA   MAIN  "
        assert normalize_project_name(name1) == normalize_project_name(name2)
        assert normalize_project_name(name2) == normalize_project_name(name3)

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_project_name("")
        with pytest.raises(ValueError, match="cannot be empty"):
            normalize_project_name("   ")

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            normalize_project_name(None)  # type: ignore
        with pytest.raises(ValueError, match="must be a string"):
            normalize_project_name(123)  # type: ignore

    def test_too_long_name_raises(self):
        with pytest.raises(ValueError, match="cannot exceed 200 characters"):
            normalize_project_name("A" * 201)

    def test_special_characters_preserved_case_folded(self):
        assert normalize_project_name("Project-Alpha_01 (Prod)") == "project-alpha_01 (prod)"
