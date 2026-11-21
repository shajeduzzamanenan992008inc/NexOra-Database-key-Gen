-- ============================================================================
-- NexOra OMEGA — Migration 010
-- Project transfer, custom platform roles, imported metadata, scheduler ledger
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only. Migrations 001–009 are unchanged and must never be edited.
--
-- This migration is additive and idempotent. It does not delete or rewrite
-- existing project, key, audit, role-assignment or scheduler data.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Separate key-type permissions from platform permissions
--
-- Migration 009 stored the declarative key permission catalogue in
-- nexora_permission_definitions. Platform RBAC uses a different namespace
-- (project:read, key:write, admin:roles, ...). Keeping both in one table made
-- custom-role validation and the Roles matrix inconsistent. The scope column
-- makes the distinction explicit without removing existing rows.
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_permission_definitions
    ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'key';

-- Migration 009 restricted permission ids to lowercase words and underscores.
-- Platform permissions use a namespace:action form (for example
-- project:delete), so the constraint is widened to allow a single colon while
-- still rejecting whitespace, uppercase, path separators and SQL punctuation.
-- Dropping and re-adding keeps this migration idempotent.
ALTER TABLE nexora_permission_definitions
    DROP CONSTRAINT IF EXISTS nexora_permission_id_check;
ALTER TABLE nexora_permission_definitions
    ADD CONSTRAINT nexora_permission_id_check
    CHECK (id ~ '^[a-z][a-z0-9_]*(:[a-z][a-z0-9_]*)?$' AND char_length(id) <= 64);

DO $nexora_permission_scope$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'nexora_permission_scope_check'
           AND conrelid = 'nexora_permission_definitions'::regclass
    ) THEN
        ALTER TABLE nexora_permission_definitions
            ADD CONSTRAINT nexora_permission_scope_check
            CHECK (scope IN ('key', 'platform'));
    END IF;
END
$nexora_permission_scope$;

INSERT INTO nexora_permission_definitions (id, name, description, risk, scope, is_system)
VALUES
    ('analytics:read',     'Read analytics',        'View platform counters, usage telemetry and key distribution.', 'standard', 'platform', true),
    ('project:read',       'Read projects',         'View projects, statistics and timelines.', 'standard', 'platform', true),
    ('project:write',      'Manage projects',       'Create, edit, archive, restore and clone projects.', 'elevated', 'platform', true),
    ('project:delete',     'Delete projects',       'Permanently delete a project and its owned records.', 'critical', 'platform', true),
    ('project:delete_all', 'Delete all projects',   'Permanently delete every project in one operation.', 'critical', 'platform', true),
    ('project:export',     'Export projects',       'Download a complete project bundle without credentials.', 'elevated', 'platform', true),
    ('project:import',     'Import projects',       'Validate and import project bundles transactionally.', 'critical', 'platform', true),
    ('key:read',           'Read keys',             'View key metadata and lifecycle history.', 'standard', 'platform', true),
    ('key:write',          'Manage keys',           'Generate, update, activate, deactivate and revoke keys.', 'elevated', 'platform', true),
    ('key:generate',       'Generate keys',         'Issue new key material. Plaintext is revealed once.', 'elevated', 'platform', true),
    ('key:activate',       'Activate keys',         'Activate a generated or deactivated key.', 'elevated', 'platform', true),
    ('key:deactivate',     'Deactivate keys',       'Temporarily deactivate a key without revoking it.', 'elevated', 'platform', true),
    ('key:revoke',         'Revoke keys',           'Permanently revoke a key and its device bindings.', 'critical', 'platform', true),
    ('key:export',         'Export key metadata',   'Export key metadata only; HMAC digests are excluded.', 'elevated', 'platform', true),
    ('key:import',         'Import keys',           'Hash and import supplied key material in one transaction.', 'critical', 'platform', true),
    ('device:read',        'Read devices',          'View device bindings and status.', 'standard', 'platform', true),
    ('device:write',       'Manage devices',        'Revoke or reset device bindings.', 'elevated', 'platform', true),
    ('webhook:read',       'Read webhooks',         'View webhook configuration and delivery history.', 'standard', 'platform', true),
    ('webhook:write',      'Manage webhooks',       'Create, edit, test, rotate or delete webhooks.', 'elevated', 'platform', true),
    ('api:access',         'API access',            'Use the management API surface.', 'elevated', 'platform', true),
    ('audit:read',         'Read audit logs',       'View administrative audit history.', 'standard', 'platform', true),
    ('notification:read',  'Read notifications',    'View the notification center.', 'standard', 'platform', true),
    ('notification:write', 'Manage notifications',  'Mark notifications read and update preferences.', 'standard', 'platform', true),
    ('automation:read',    'Read automation',       'View automation execution history.', 'standard', 'platform', true),
    ('automation:run',     'Run automation',        'Execute an idempotent automation sweep.', 'elevated', 'platform', true),
    ('admin:read',         'Read administrators',   'View administrator accounts and role assignments.', 'standard', 'platform', true),
    ('admin:roles',        'Manage roles',          'Create, edit, clone, delete and assign platform roles.', 'critical', 'platform', true),
    ('admin:sessions',     'Manage sessions',       'Revoke administrator sessions.', 'critical', 'platform', true),
    ('admin:manage',       'Manage administrators', 'Manage administrator accounts.', 'critical', 'platform', true),
    ('system:read',        'Read system status',    'View health and readiness information.', 'standard', 'platform', true),
    ('system:settings',    'Manage system settings','Change platform-wide preferences.', 'critical', 'platform', true),
    ('security:settings',  'Manage security',       'Change security configuration.', 'critical', 'platform', true),
    ('import:write',       'Import data',           'Import validated platform data.', 'critical', 'platform', true),
    ('export:read',        'Export data',           'Download platform inventory and bundles.', 'elevated', 'platform', true)
ON CONFLICT (id) DO UPDATE
    SET name = EXCLUDED.name,
        description = EXCLUDED.description,
        risk = EXCLUDED.risk,
        scope = EXCLUDED.scope,
        is_system = true;

UPDATE nexora_permission_definitions
   SET scope = 'key'
 WHERE scope IS NULL;

-- ----------------------------------------------------------------------------
-- 2. Custom platform roles
--
-- Built-in roles remain code-defined and immutable. This table stores only
-- custom roles. A role is never deleted while an administrator references it.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_roles (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    description   text NOT NULL DEFAULT '',
    permissions   jsonb NOT NULL DEFAULT '[]'::jsonb,
    is_system     boolean NOT NULL DEFAULT false,
    created_by    uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_roles_id_check
        CHECK (id ~ '^[a-z][a-z0-9_]{2,49}$'),
    CONSTRAINT nexora_roles_name_check
        CHECK (char_length(name) BETWEEN 1 AND 80),
    CONSTRAINT nexora_roles_description_check
        CHECK (char_length(description) <= 500),
    CONSTRAINT nexora_roles_permissions_array
        CHECK (jsonb_typeof(permissions) = 'array'),
    CONSTRAINT nexora_roles_permissions_size
        CHECK (jsonb_array_length(permissions) <= 64)
);

CREATE INDEX IF NOT EXISTS ix_nexora_roles_system
    ON nexora_roles (is_system, name);

DROP TRIGGER IF EXISTS trg_nexora_roles_updated_at ON nexora_roles;
CREATE TRIGGER trg_nexora_roles_updated_at
    BEFORE UPDATE ON nexora_roles
    FOR EACH ROW
    EXECUTE FUNCTION nexora_set_updated_at();

ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS platform_role_id text
        REFERENCES nexora_roles(id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS ix_admin_users_platform_role_id
    ON admin_users (platform_role_id)
    WHERE platform_role_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 3. Imported key/installation metadata
--
-- Export never contains key hashes, plaintext, device tokens or webhook
-- secrets. Imported key and installation metadata is retained here so an
-- operator can see what must be regenerated. It is deliberately separate from
-- reader_keys, which only accepts real hashed credentials.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_imported_key_records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE CASCADE,
    record_type     text NOT NULL DEFAULT 'key',
    source_id       text NOT NULL DEFAULT '',
    key_type_id     text NOT NULL DEFAULT '',
    label           text NOT NULL DEFAULT '',
    key_hint        text NOT NULL DEFAULT '',
    fingerprint     text NOT NULL DEFAULT '',
    status          text NOT NULL DEFAULT 'imported',
    requires_regeneration boolean NOT NULL DEFAULT true,
    data            jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_by     uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    imported_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_imported_records_type_check
        CHECK (record_type IN ('key', 'installation')),
    CONSTRAINT nexora_imported_records_status_check
        CHECK (char_length(status) <= 40),
    CONSTRAINT nexora_imported_records_data_object
        CHECK (jsonb_typeof(data) = 'object')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nexora_imported_records_source
    ON nexora_imported_key_records (project_id, record_type, source_id)
    WHERE source_id <> '';
CREATE INDEX IF NOT EXISTS ix_nexora_imported_records_project
    ON nexora_imported_key_records (project_id, imported_at DESC);

-- ----------------------------------------------------------------------------
-- 4. Scheduler execution metadata and retry indexes
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_automation_runs
    ADD COLUMN IF NOT EXISTS worker_id text NOT NULL DEFAULT '';
ALTER TABLE nexora_automation_runs
    ADD COLUMN IF NOT EXISTS duration_ms integer;
ALTER TABLE nexora_automation_runs
    ADD COLUMN IF NOT EXISTS dry_run boolean NOT NULL DEFAULT false;

DO $nexora_automation_duration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'nexora_automation_duration_check'
           AND conrelid = 'nexora_automation_runs'::regclass
    ) THEN
        ALTER TABLE nexora_automation_runs
            ADD CONSTRAINT nexora_automation_duration_check
            CHECK (duration_ms IS NULL OR duration_ms >= 0);
    END IF;
END
$nexora_automation_duration$;

CREATE INDEX IF NOT EXISTS ix_nexora_automation_runs_status
    ON nexora_automation_runs (status, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_webhook_delivery_retry
    ON nexora_webhook_deliveries (status, next_attempt_at, attempts)
    WHERE status IN ('pending', 'failed');

ALTER TABLE nexora_webhook_deliveries
    ADD COLUMN IF NOT EXISTS response_bytes integer;
ALTER TABLE nexora_webhook_deliveries
    ADD COLUMN IF NOT EXISTS last_attempt_at timestamptz;

-- ----------------------------------------------------------------------------
-- 5. Deny-by-default RLS for new browser-facing tables
-- ----------------------------------------------------------------------------
DO $nexora_transfer_rls$
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
        'nexora_roles', 'nexora_imported_key_records'
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
$nexora_transfer_rls$;

COMMIT;
