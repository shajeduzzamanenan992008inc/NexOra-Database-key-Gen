-- ============================================================================
-- NexOra OMEGA — Migration 012
-- Global timezone preference
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only. Migrations 001–011 are unchanged and must never be edited.
-- This migration is additive and idempotent. It only inserts a default value
-- when the setting is absent; it never rewrites an existing preference and
-- never touches stored timestamps (all timestamps remain canonical UTC).
-- ============================================================================

BEGIN;

INSERT INTO system_settings (key, value, description, is_secret)
VALUES (
    'nexora.display_timezone',
    'UTC',
    'Global IANA timezone used to display and interpret local date/time input. Stored timestamps remain UTC.',
    false
)
ON CONFLICT (key) DO NOTHING;

COMMIT;
