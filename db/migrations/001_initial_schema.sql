-- ============================================================================
-- RH E-SPORTS — Secure Key Generator & License Management System
-- Migration 001: initial schema
-- Target: PostgreSQL 13+ / Supabase
--
-- Plaintext license keys are never stored. license_keys.key_hash holds an
-- HMAC-SHA256 digest keyed by the server-side KEY_PEPPER.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- Administrators
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_users (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email                 text NOT NULL,
    full_name             text NOT NULL DEFAULT '',
    password_hash         text NOT NULL,
    role                  text NOT NULL DEFAULT 'admin',
    is_active             boolean NOT NULL DEFAULT true,
    failed_login_attempts integer NOT NULL DEFAULT 0,
    locked_until          timestamptz,
    last_login_at         timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT admin_users_role_check
        CHECK (role IN ('owner', 'admin', 'viewer')),
    CONSTRAINT admin_users_email_check
        CHECK (char_length(email) <= 254 AND position('@' IN email) > 1),
    CONSTRAINT admin_users_failed_login_check
        CHECK (failed_login_attempts >= 0)
);

COMMENT ON TABLE admin_users IS
    'Administrator accounts. Passwords are scrypt hashes; roles are enforced server-side.';

-- ----------------------------------------------------------------------------
-- Server-side admin sessions (opaque token, stored as SHA-256)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_sessions (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id uuid NOT NULL REFERENCES admin_users(id) ON DELETE CASCADE,
    token_hash    text NOT NULL,
    csrf_hash     text NOT NULL,
    ip_address    text,
    user_agent    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    CONSTRAINT admin_sessions_token_hash_check CHECK (char_length(token_hash) = 64),
    CONSTRAINT admin_sessions_csrf_hash_check  CHECK (char_length(csrf_hash) = 64)
);

COMMENT ON TABLE admin_sessions IS
    'Opaque server-side admin sessions. Only token and CSRF hashes are stored.';

-- ----------------------------------------------------------------------------
-- License keys
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS license_keys (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash         text NOT NULL,
    key_format       text NOT NULL,
    key_hint         text NOT NULL DEFAULT '',
    key_prefix       text NOT NULL DEFAULT '',
    key_suffix       text NOT NULL DEFAULT '',
    label            text NOT NULL DEFAULT '',
    status           text NOT NULL DEFAULT 'active',
    max_activations  integer NOT NULL DEFAULT 1,
    activation_count integer NOT NULL DEFAULT 0,
    expires_at       timestamptz,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    batch_id         uuid,
    created_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    revoked_by       uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    revoked_reason   text,
    CONSTRAINT license_keys_status_check
        CHECK (status IN ('active', 'inactive', 'pending', 'expired', 'revoked', 'used')),
    CONSTRAINT license_keys_max_activations_check
        CHECK (max_activations BETWEEN 1 AND 10000),
    CONSTRAINT license_keys_activation_count_check
        CHECK (activation_count >= 0),
    CONSTRAINT license_keys_hash_check
        CHECK (char_length(key_hash) = 64)
);

COMMENT ON COLUMN license_keys.key_hash IS
    'HMAC-SHA256(KEY_PEPPER, normalized key). Deterministic for indexed lookup; plaintext is never stored.';
COMMENT ON COLUMN license_keys.key_suffix IS
    'Last random characters, used only for human-readable masking.';
COMMENT ON COLUMN license_keys.key_hint IS
    'Masked display form; the only key representation returned after generation.';
COMMENT ON COLUMN license_keys.activation_count IS
    'Incremented only inside a row-locked activation transaction.';

-- ----------------------------------------------------------------------------
-- Device/account activations (one row per successful activation)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS key_activations (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    license_key_id     uuid NOT NULL REFERENCES license_keys(id) ON DELETE CASCADE,
    device_hash        text NOT NULL,
    device_hint        text NOT NULL DEFAULT '',
    account_hash       text,
    account_hint       text,
    status             text NOT NULL DEFAULT 'active',
    ip_hash            text,
    user_agent         text,
    activated_at       timestamptz NOT NULL DEFAULT now(),
    deactivated_at     timestamptz,
    deactivated_reason text,
    CONSTRAINT key_activations_status_check
        CHECK (status IN ('active', 'deactivated')),
    CONSTRAINT key_activations_device_hash_check
        CHECK (char_length(device_hash) = 64)
);

COMMENT ON COLUMN key_activations.device_hash IS
    'HMAC-SHA256 of the device identifier; the raw device identifier is not stored.';

-- ----------------------------------------------------------------------------
-- Generation history (one row per generation request, shared by the batch)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generation_logs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id        uuid NOT NULL,
    admin_user_id   uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    quantity        integer NOT NULL,
    key_format      text NOT NULL,
    key_prefix      text NOT NULL DEFAULT '',
    key_suffix      text NOT NULL DEFAULT '',
    groups          integer NOT NULL,
    group_length    integer NOT NULL,
    separator       text NOT NULL DEFAULT '-',
    max_activations integer NOT NULL,
    expires_at      timestamptz,
    label           text NOT NULL DEFAULT '',
    ip_address      text,
    user_agent      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT generation_logs_quantity_check
        CHECK (quantity BETWEEN 1 AND 2000),
    CONSTRAINT generation_logs_groups_check
        CHECK (groups BETWEEN 1 AND 8),
    CONSTRAINT generation_logs_group_length_check
        CHECK (group_length BETWEEN 3 AND 10)
);

-- ----------------------------------------------------------------------------
-- Administrative audit trail
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_audit_logs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    actor_email   text,
    action        text NOT NULL,
    target_type   text,
    target_id     text,
    outcome       text NOT NULL DEFAULT 'success',
    ip_address    text,
    user_agent    text,
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT admin_audit_logs_outcome_check
        CHECK (outcome IN ('success', 'failure'))
);

-- ----------------------------------------------------------------------------
-- updated_at maintenance
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.rh_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
CREATE TRIGGER trg_admin_users_updated_at
    BEFORE UPDATE ON admin_users
    FOR EACH ROW EXECUTE FUNCTION public.rh_set_updated_at();

DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;
CREATE TRIGGER trg_license_keys_updated_at
    BEFORE UPDATE ON license_keys
    FOR EACH ROW EXECUTE FUNCTION public.rh_set_updated_at();

COMMIT;
