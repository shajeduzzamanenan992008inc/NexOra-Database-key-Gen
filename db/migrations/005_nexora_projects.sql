-- ============================================================================
-- NexOra Database Key Generator
-- Migration 005: NexOra Project + Reader Key Management
-- Target: PostgreSQL 13+ / Supabase
--
-- This migration adds the project-based reader key architecture.
-- Plaintext reader keys are never stored. reader_keys.key_hash holds an
-- HMAC-SHA256 digest keyed by the server-side KEY_PEPPER.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Projects
-- NexOra manages multiple database projects. Each project can have reader keys.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_projects (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_name     text NOT NULL,
    project_name_normalized text NOT NULL,  -- lowercase for uniqueness
    database_url_ref text NOT NULL DEFAULT '',  -- reference identifier, NOT credentials
    status           text NOT NULL DEFAULT 'active',
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz,  -- soft delete
    created_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    CONSTRAINT nexora_projects_status_check
        CHECK (status IN ('active', 'inactive', 'deleted')),
    CONSTRAINT nexora_projects_name_length_check
        CHECK (char_length(project_name) BETWEEN 1 AND 200),
    CONSTRAINT nexora_projects_name_normalized_check
        CHECK (char_length(project_name_normalized) BETWEEN 1 AND 200)
);

COMMENT ON TABLE nexora_projects IS
    'NexOra managed projects. Soft-deleted rows are retained for audit history.';
COMMENT ON COLUMN nexora_projects.project_name_normalized IS
    'Lowercase project name used for case-insensitive uniqueness enforcement.';
COMMENT ON COLUMN nexora_projects.database_url_ref IS
    'Project database URL reference identifier. Full credentials are never stored here.';

-- Case-insensitive project name uniqueness (only among non-deleted projects)
CREATE UNIQUE INDEX IF NOT EXISTS ux_nexora_projects_name_normalized
    ON nexora_projects (project_name_normalized)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_nexora_projects_status
    ON nexora_projects (status, created_at DESC);

-- Auto-update updated_at
DROP TRIGGER IF EXISTS trg_nexora_projects_updated_at ON nexora_projects;
CREATE TRIGGER trg_nexora_projects_updated_at
    BEFORE UPDATE ON nexora_projects
    FOR EACH ROW EXECUTE FUNCTION public.rh_set_updated_at();

-- ----------------------------------------------------------------------------
-- Reader Keys
-- One-time activation keys scoped to a project.
-- key_hash = HMAC-SHA256(KEY_PEPPER, normalized_key). Never store plaintext.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reader_keys (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE RESTRICT,
    key_hash         text NOT NULL,
    key_hint         text NOT NULL DEFAULT '',  -- masked display form
    label            text NOT NULL DEFAULT '',
    status           text NOT NULL DEFAULT 'generated',
    is_permanent     boolean NOT NULL DEFAULT false,
    expires_at       timestamptz,  -- NULL = permanent or not yet expired
    batch_id         uuid,
    created_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    used_at          timestamptz,
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    revoked_reason   text,
    CONSTRAINT reader_keys_status_check
        CHECK (status IN ('generated', 'used', 'expired', 'revoked')),
    CONSTRAINT reader_keys_hash_check
        CHECK (char_length(key_hash) = 64),
    CONSTRAINT reader_keys_state_check
        CHECK (
            (status = 'generated' AND used_at IS NULL AND revoked_at IS NULL)
            OR (status = 'used' AND used_at IS NOT NULL AND revoked_at IS NULL)
            OR (status = 'expired' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL)
        )
);

COMMENT ON TABLE reader_keys IS
    'One-time reader activation keys scoped to a project. HMAC digests only; plaintext never stored.';
COMMENT ON COLUMN reader_keys.key_hash IS
    'HMAC-SHA256(KEY_PEPPER, normalized_key). Used for indexed lookup; plaintext never stored.';
COMMENT ON COLUMN reader_keys.is_permanent IS
    'If true, the key does not expire by time. Status drives lifecycle.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_keys_key_hash
    ON reader_keys (key_hash);

CREATE INDEX IF NOT EXISTS ix_reader_keys_project_status
    ON reader_keys (project_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_reader_keys_expires_at
    ON reader_keys (expires_at)
    WHERE expires_at IS NOT NULL AND status = 'generated';

CREATE INDEX IF NOT EXISTS ix_reader_keys_batch
    ON reader_keys (batch_id)
    WHERE batch_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Reader Installations
-- Records the first (and only) successful activation of a reader key.
-- Enforces one-installation-per-project.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reader_installations (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE RESTRICT,
    reader_key_id    uuid NOT NULL REFERENCES reader_keys(id) ON DELETE RESTRICT,
    device_token_hash text NOT NULL,  -- HMAC-SHA256 of issued credential; cleared on revocation
    device_id        text,  -- reader-supplied device identifier
    status           text NOT NULL DEFAULT 'active',
    activated_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz,
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    revoked_reason   text,
    ip_address       text,
    user_agent       text,
    CONSTRAINT reader_installations_status_check
        CHECK (status IN ('active', 'revoked')),
    CONSTRAINT reader_installations_token_hash_check
        CHECK (device_token_hash = '' OR char_length(device_token_hash) = 64),
    CONSTRAINT reader_installations_state_check
        CHECK (
            (status = 'active' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL AND device_token_hash = '')
        )
);

COMMENT ON TABLE reader_installations IS
    'Active reader app installations. One active installation per project enforced by unique index.';
COMMENT ON COLUMN reader_installations.device_token_hash IS
    'HMAC-SHA256 of the high-entropy device credential. Cleared (empty string) on revocation.';

-- One active installation per project
CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_installations_project_active
    ON reader_installations (project_id)
    WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_installations_key
    ON reader_installations (reader_key_id);

-- Partial unique index: active token hashes must be unique
CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_installations_token_active
    ON reader_installations (device_token_hash)
    WHERE status = 'active' AND device_token_hash != '';

CREATE INDEX IF NOT EXISTS ix_reader_installations_project_status
    ON reader_installations (project_id, status);

-- ----------------------------------------------------------------------------
-- Device Tokens (heartbeat/reconnect tracking)
-- Issued once per activation. The hash is the lookup key.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS device_tokens (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id  uuid NOT NULL REFERENCES reader_installations(id) ON DELETE CASCADE,
    project_id       uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE RESTRICT,
    token_hash       text NOT NULL,  -- HMAC-SHA256 of the issued token
    issued_at        timestamptz NOT NULL DEFAULT now(),
    last_used_at     timestamptz,
    rotated_at       timestamptz,
    is_active        boolean NOT NULL DEFAULT true,
    CONSTRAINT device_tokens_hash_check
        CHECK (char_length(token_hash) = 64)
);

COMMENT ON TABLE device_tokens IS
    'Device credential tokens for Reader App reconnection. Only hashes stored.';

CREATE UNIQUE INDEX IF NOT EXISTS ux_device_tokens_hash
    ON device_tokens (token_hash)
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS ix_device_tokens_installation
    ON device_tokens (installation_id, issued_at DESC);

-- ----------------------------------------------------------------------------
-- System Settings
-- Key-value store for runtime configuration.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_settings (
    key              text PRIMARY KEY,
    value            text NOT NULL DEFAULT '',
    description      text NOT NULL DEFAULT '',
    is_secret        boolean NOT NULL DEFAULT false,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    updated_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL
);

COMMENT ON TABLE system_settings IS
    'Runtime system configuration. Secret values should not be stored in plaintext.';

-- Seed default settings
INSERT INTO system_settings (key, value, description, is_secret)
VALUES
    ('nexora.version', '1.0.0', 'NexOra system version', false),
    ('nexora.max_installations_per_project', '1', 'Maximum active reader installations per project', false),
    ('nexora.heartbeat_interval_seconds', '300', 'Expected reader heartbeat interval in seconds', false)
ON CONFLICT (key) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Audit logs update: add project_id reference
-- ----------------------------------------------------------------------------
ALTER TABLE admin_audit_logs
    ADD COLUMN IF NOT EXISTS project_id uuid REFERENCES nexora_projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_project
    ON admin_audit_logs (project_id, created_at DESC)
    WHERE project_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- RLS: deny-by-default for Supabase browser roles
-- ----------------------------------------------------------------------------
DO $nexora_rls$
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
        'nexora_projects',
        'reader_keys',
        'reader_installations',
        'device_tokens',
        'system_settings'
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
$nexora_rls$;

COMMIT;
