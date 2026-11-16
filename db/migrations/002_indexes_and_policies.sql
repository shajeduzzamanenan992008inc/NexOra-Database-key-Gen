-- ============================================================================
-- RH E-SPORTS — Secure Key Generator & License Management System
-- Migration 002: indexes, uniqueness, row-level security, expiry helper
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Uniqueness
-- ----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_admin_users_email_lower
    ON admin_users (lower(email));

CREATE UNIQUE INDEX IF NOT EXISTS ux_admin_sessions_token_hash
    ON admin_sessions (token_hash);

CREATE UNIQUE INDEX IF NOT EXISTS ux_license_keys_key_hash
    ON license_keys (key_hash);

-- Replay protection: a device can hold only one activation row per license.
CREATE UNIQUE INDEX IF NOT EXISTS ux_key_activations_key_device
    ON key_activations (license_key_id, device_hash);

-- ----------------------------------------------------------------------------
-- Query indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_license_keys_status_created
    ON license_keys (status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_license_keys_created_at
    ON license_keys (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_license_keys_expires_at
    ON license_keys (expires_at)
    WHERE expires_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_license_keys_label_lower
    ON license_keys (lower(label));

CREATE INDEX IF NOT EXISTS ix_license_keys_batch
    ON license_keys (batch_id);

CREATE INDEX IF NOT EXISTS ix_key_activations_key_status
    ON key_activations (license_key_id, status);

CREATE INDEX IF NOT EXISTS ix_key_activations_activated_at
    ON key_activations (activated_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_sessions_user
    ON admin_sessions (admin_user_id, expires_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_sessions_expires
    ON admin_sessions (expires_at);

CREATE INDEX IF NOT EXISTS ix_generation_logs_created_at
    ON generation_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_generation_logs_admin
    ON generation_logs (admin_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_created_at
    ON admin_audit_logs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_action
    ON admin_audit_logs (action, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_actor
    ON admin_audit_logs (admin_user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_target
    ON admin_audit_logs (target_type, target_id, created_at DESC);

-- ----------------------------------------------------------------------------
-- Row-level security: deny by default for every non-owner role.
--
-- The application connects as a trusted server role (Supabase `postgres`, or a
-- dedicated role with BYPASSRLS). Browser clients never reach PostgreSQL
-- directly, and the anon/authenticated Supabase roles receive no policies and
-- no table privileges. RLS is intentionally not FORCEd, because the owner role
-- must be able to operate without policies.
-- ----------------------------------------------------------------------------
DO $rls$
DECLARE
    tbl text;
    has_anon boolean;
    has_authenticated boolean;
BEGIN
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') INTO has_anon;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') INTO has_authenticated;

    FOREACH tbl IN ARRAY ARRAY[
        'admin_users',
        'admin_sessions',
        'license_keys',
        'key_activations',
        'generation_logs',
        'admin_audit_logs'
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
    END LOOP;
END
$rls$;

-- ----------------------------------------------------------------------------
-- Lazy expiry helper.
--
-- Called before list/stats reads and inside activation transactions. Keeping
-- expiry in the database avoids application clock drift.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.rh_expire_overdue_keys()
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    expired_count integer;
BEGIN
    UPDATE public.license_keys
       SET status = 'expired'
     WHERE status IN ('active', 'inactive', 'pending')
       AND expires_at IS NOT NULL
       AND expires_at <= now();
    GET DIAGNOSTICS expired_count = ROW_COUNT;
    RETURN expired_count;
END;
$$;

REVOKE ALL ON FUNCTION public.rh_expire_overdue_keys() FROM PUBLIC;
DO $revoke$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON FUNCTION public.rh_expire_overdue_keys() FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON FUNCTION public.rh_expire_overdue_keys() FROM authenticated;
    END IF;
END
$revoke$;

COMMIT;
