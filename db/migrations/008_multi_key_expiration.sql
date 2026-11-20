-- ============================================================================
-- NexOra Key Generator
-- Migration 008: multi-key projects, explicit expiration, name reuse
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only. Migrations 001-007 are not modified.
--
-- Changes:
--   1. Drop the one-reader-key-per-project unique index.
--   2. Remove the permanent deleted-project-name tombstone registry so a
--      permanently deleted project name can be reused.
--   3. Add reader_keys.expiration_preset, storing the administrator's choice
--      explicitly (1/7/30/90 days, 1 year, custom, or never).
--   4. Enforce consistency between expiration_preset, is_permanent and
--      expires_at so a "never" key can never carry an expiry date.
--   5. Add an index for the per-project key list.
--
-- Existing valid reader keys are preserved:
--   * is_permanent = true and expires_at IS NULL  -> expiration_preset='never'
--   * is_permanent = false with a future/past date -> expiration_preset='custom'
--   * a legacy row with neither a permanent flag nor a date is made permanent
--     rather than being silently expired.
--
-- Rollback (manual, isolated environments only; destructive to multi-key data):
--   DROP INDEX IF EXISTS ix_reader_keys_project_created;
--   ALTER TABLE reader_keys DROP CONSTRAINT IF EXISTS reader_keys_expiration_state_check;
--   ALTER TABLE reader_keys DROP CONSTRAINT IF EXISTS reader_keys_expiration_preset_check;
--   ALTER TABLE reader_keys DROP COLUMN IF EXISTS expiration_preset;
--   CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_keys_project_single
--       ON reader_keys (project_id);
--   (The tombstone table cannot be restored with its historical rows.)
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Unlimited keys per project
-- ----------------------------------------------------------------------------
DROP INDEX IF EXISTS ux_reader_keys_project_single;

CREATE INDEX IF NOT EXISTS ix_reader_keys_project_created
    ON reader_keys (project_id, created_at DESC);

COMMENT ON INDEX ix_reader_keys_project_created IS
    'Supports the per-project reader key list; a project may own unlimited keys.';

-- ----------------------------------------------------------------------------
-- 2. Project names may be reused after permanent deletion
--    The table only stored deleted names; audit snapshots remain in
--    admin_audit_logs with project_id = NULL.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS nexora_deleted_project_names;

-- ----------------------------------------------------------------------------
-- 3. Explicit per-key expiration choice
-- ----------------------------------------------------------------------------
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS expiration_preset text;

-- Preserve legacy rows before adding the NOT NULL/default and checks.
UPDATE reader_keys
   SET is_permanent = true,
       expires_at = NULL
 WHERE is_permanent = false
   AND expires_at IS NULL;

UPDATE reader_keys
   SET is_permanent = false
 WHERE is_permanent = true
   AND expires_at IS NOT NULL;

UPDATE reader_keys
   SET expiration_preset = CASE
           WHEN is_permanent THEN 'never'
           ELSE 'custom'
       END
 WHERE expiration_preset IS NULL;

ALTER TABLE reader_keys
    ALTER COLUMN expiration_preset SET DEFAULT 'never';

ALTER TABLE reader_keys
    ALTER COLUMN expiration_preset SET NOT NULL;

ALTER TABLE reader_keys
    DROP CONSTRAINT IF EXISTS reader_keys_expiration_preset_check;

ALTER TABLE reader_keys
    ADD CONSTRAINT reader_keys_expiration_preset_check
    CHECK (expiration_preset IN (
        '1_day', '7_days', '30_days', '90_days', '1_year', 'custom', 'never'
    ));

ALTER TABLE reader_keys
    DROP CONSTRAINT IF EXISTS reader_keys_expiration_state_check;

ALTER TABLE reader_keys
    ADD CONSTRAINT reader_keys_expiration_state_check
    CHECK (
        (expiration_preset = 'never'
            AND is_permanent = true
            AND expires_at IS NULL)
        OR
        (expiration_preset <> 'never'
            AND is_permanent = false
            AND expires_at IS NOT NULL)
    );

COMMENT ON COLUMN reader_keys.expiration_preset IS
    'Administrator-selected expiration: 1_day, 7_days, 30_days, 90_days, 1_year, custom, or never.';
COMMENT ON COLUMN reader_keys.is_permanent IS
    'Derived compatibility flag: true only when expiration_preset = never.';

COMMIT;
