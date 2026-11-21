-- ============================================================================
-- NexOra OMEGA — Migration 011
-- Project-scoped custom roles and imported-key replacement history
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only. Migrations 001–010 are unchanged and must never be edited.
-- This migration is additive and idempotent. It never rewrites or deletes
-- existing roles, assignments, projects, keys, or imported metadata.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Project-scoped custom roles
--
-- A NULL project_id keeps the existing global custom-role behaviour. A non-NULL
-- project_id makes the role assignable only inside that project. Built-in roles
-- remain code-defined and are never stored in this table.
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_roles
    ADD COLUMN IF NOT EXISTS project_id uuid
        REFERENCES nexora_projects(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_nexora_roles_project
    ON nexora_roles (project_id, name)
    WHERE project_id IS NOT NULL;

-- Multiple project roles may be assigned to one administrator. Assignments do
-- not modify admin_users.platform_role, so a project role can never grant a
-- global permission.
CREATE TABLE IF NOT EXISTS nexora_project_role_assignments (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id   uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE CASCADE,
    admin_id     uuid NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    role_id      text NOT NULL REFERENCES nexora_roles(id) ON DELETE RESTRICT,
    assigned_by  uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_project_role_assignments_unique
        UNIQUE (project_id, admin_id, role_id)
);

CREATE INDEX IF NOT EXISTS ix_nexora_project_role_assignments_admin
    ON nexora_project_role_assignments (admin_id, project_id);
CREATE INDEX IF NOT EXISTS ix_nexora_project_role_assignments_role
    ON nexora_project_role_assignments (role_id);

-- ----------------------------------------------------------------------------
-- 2. Imported-key replacement history
--
-- Imported metadata is never turned into an original credential. Each
-- replacement is a newly generated reader_keys row linked to its source
-- metadata. History rows preserve an auditable chain even when an operator is
-- explicitly allowed to generate more than one replacement.
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_imported_key_records
    ADD COLUMN IF NOT EXISTS replacement_key_id uuid
        REFERENCES reader_keys(id) ON DELETE SET NULL;
ALTER TABLE nexora_imported_key_records
    ADD COLUMN IF NOT EXISTS replaced_at timestamptz;
ALTER TABLE nexora_imported_key_records
    ADD COLUMN IF NOT EXISTS replacement_count integer NOT NULL DEFAULT 0;

DO $nexora_imported_replacement_count$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'nexora_imported_replacement_count_check'
           AND conrelid = 'nexora_imported_key_records'::regclass
    ) THEN
        ALTER TABLE nexora_imported_key_records
            ADD CONSTRAINT nexora_imported_replacement_count_check
            CHECK (replacement_count >= 0);
    END IF;
END
$nexora_imported_replacement_count$;

CREATE TABLE IF NOT EXISTS nexora_imported_key_replacements (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    imported_record_id uuid NOT NULL
        REFERENCES nexora_imported_key_records(id) ON DELETE CASCADE,
    project_id         uuid NOT NULL
        REFERENCES nexora_projects(id) ON DELETE CASCADE,
    key_id             uuid REFERENCES reader_keys(id) ON DELETE SET NULL,
    batch_id           uuid,
    warnings           jsonb NOT NULL DEFAULT '[]'::jsonb,
    generated_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_imported_replacements_warnings_array
        CHECK (jsonb_typeof(warnings) = 'array')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nexora_imported_replacements_key
    ON nexora_imported_key_replacements (key_id)
    WHERE key_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_nexora_imported_replacements_record
    ON nexora_imported_key_replacements (imported_record_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_imported_replacements_project
    ON nexora_imported_key_replacements (project_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- 3. Deny-by-default RLS for the new browser-facing tables
-- ----------------------------------------------------------------------------
DO $nexora_replacement_rls$
DECLARE
    tbl text;
    has_anon boolean;
    has_authenticated boolean;
    has_service_role boolean;
BEGIN
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') INTO has_anon;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') INTO has_authenticated;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') INTO has_service_role;

    FOREACH tbl IN ARRAY ARRAY[
        'nexora_project_role_assignments', 'nexora_imported_key_replacements'
    ]
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl);
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC', tbl);
        IF has_anon THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM anon', tbl);
        END IF;
        IF has_authenticated THEN
            EXECUTE format('REVOKE ALL ON TABLE public.%I FROM authenticated', tbl);
        END IF;
        IF has_service_role THEN
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.%I TO service_role', tbl);
        END IF;
    END LOOP;
END
$nexora_replacement_rls$;

COMMIT;
