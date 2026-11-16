-- ============================================================================
-- OMEGA NEX-ORA — Migration 007: Project / Key Management Upgrade
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only migration. Historical migrations 001–006 are not modified.
--
-- Adds:
--   1. One-reader-key-per-project uniqueness (reader_keys.project_id).
--   2. nexora_projects.auto_delete_at (timezone-aware scheduled deletion).
--   3. nexora_deleted_project_names tombstone registry so a hard-deleted
--      project name cannot silently be reused.
--   4. Supporting indexes and RLS posture for the tombstone table.
--
-- Safety:
--   * Preflight checks stop with a descriptive exception when legacy data is
--     ambiguous (duplicate normalized project names or multiple reader keys
--     for one project). Nothing is deleted or merged automatically.
--   * Every statement is transactional and idempotent where practical.
--   * Legacy SMS Reader tables (sms_reader_keys, sms_reader_events) and the
--     unrelated license-key tables are not touched.
--
-- Rollback (manual, only for an isolated environment):
--   DROP INDEX IF EXISTS ux_reader_keys_project_single;
--   DROP TABLE IF EXISTS nexora_deleted_project_names;
--   DROP INDEX IF EXISTS ix_nexora_projects_auto_delete;
--   ALTER TABLE nexora_projects DROP COLUMN IF EXISTS auto_delete_at;
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Preflight: refuse to continue on ambiguous legacy data
-- ----------------------------------------------------------------------------
DO $preflight$
DECLARE
    duplicate_names integer;
    duplicate_keys integer;
    example_name text;
    example_project text;
BEGIN
    SELECT count(*), min(project_name_normalized)
      INTO duplicate_names, example_name
      FROM (
          SELECT project_name_normalized
            FROM nexora_projects
           WHERE deleted_at IS NULL
           GROUP BY project_name_normalized
          HAVING count(*) > 1
      ) AS duplicates;

    IF duplicate_names > 0 THEN
        RAISE EXCEPTION
            'Migration 007 aborted: % duplicate live project name(s) exist (example: %). Reconcile them manually before retrying.',
            duplicate_names, example_name;
    END IF;

    SELECT count(*), min(project_id::text)
      INTO duplicate_keys, example_project
      FROM (
          SELECT project_id
            FROM reader_keys
           GROUP BY project_id
          HAVING count(*) > 1
      ) AS duplicates;

    IF duplicate_keys > 0 THEN
        RAISE EXCEPTION
            'Migration 007 aborted: % project(s) already have multiple reader keys (example project_id: %). Reconcile them manually before retrying.',
            duplicate_keys, example_project;
    END IF;
END
$preflight$;

-- ----------------------------------------------------------------------------
-- 1. Exactly one reader key per project
-- ----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_keys_project_single
    ON reader_keys (project_id);

COMMENT ON INDEX ux_reader_keys_project_single IS
    'Enforces the one-project-one-reader-key rule at the database level.';

-- ----------------------------------------------------------------------------
-- 2. Scheduled auto-delete timestamp
--    NULL means no automatic deletion is scheduled. There is intentionally no
--    default retention period; the administrator must configure one.
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS auto_delete_at timestamptz;

COMMENT ON COLUMN nexora_projects.auto_delete_at IS
    'Scheduled hard-delete timestamp. NULL means no automatic deletion is scheduled.';

CREATE INDEX IF NOT EXISTS ix_nexora_projects_auto_delete
    ON nexora_projects (auto_delete_at)
    WHERE auto_delete_at IS NOT NULL AND deleted_at IS NULL;

-- ----------------------------------------------------------------------------
-- 3. Deleted project-name tombstones
--    Stores only the project name and deletion metadata. No key material,
--    device credential, or project-owned record is retained here.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_deleted_project_names (
    project_name_normalized text PRIMARY KEY,
    project_name            text NOT NULL,
    deleted_at              timestamptz NOT NULL DEFAULT now(),
    deleted_by              uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    details                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT nexora_deleted_names_length_check
        CHECK (char_length(project_name) BETWEEN 1 AND 200),
    CONSTRAINT nexora_deleted_names_normalized_length_check
        CHECK (char_length(project_name_normalized) BETWEEN 1 AND 200)
);

COMMENT ON TABLE nexora_deleted_project_names IS
    'Tombstone registry preventing reuse of permanently deleted project names. Contains no Reader Key plaintext or HMAC.';

CREATE INDEX IF NOT EXISTS ix_nexora_deleted_project_names_deleted_at
    ON nexora_deleted_project_names (deleted_at DESC);

-- ----------------------------------------------------------------------------
-- 4. Row-level security: deny-by-default for Supabase browser roles
-- ----------------------------------------------------------------------------
DO $nexora_deleted_rls$
DECLARE
    has_anon boolean;
    has_authenticated boolean;
    has_service_role boolean;
BEGIN
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') INTO has_anon;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') INTO has_authenticated;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') INTO has_service_role;

    EXECUTE 'ALTER TABLE public.nexora_deleted_project_names ENABLE ROW LEVEL SECURITY';
    EXECUTE 'REVOKE ALL ON TABLE public.nexora_deleted_project_names FROM PUBLIC';
    IF has_anon THEN
        EXECUTE 'REVOKE ALL ON TABLE public.nexora_deleted_project_names FROM anon';
    END IF;
    IF has_authenticated THEN
        EXECUTE 'REVOKE ALL ON TABLE public.nexora_deleted_project_names FROM authenticated';
    END IF;
    IF has_service_role THEN
        EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.nexora_deleted_project_names TO service_role';
    END IF;
END
$nexora_deleted_rls$;

COMMIT;
