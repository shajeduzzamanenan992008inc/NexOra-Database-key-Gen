-- ============================================================================
-- NexOra-Database-key-Gen
-- SMS Reader One-Time Key Management
-- Migration 004: isolated SMS reader key and activation-history schema
-- Target: PostgreSQL 13+ / Supabase
--
-- Plaintext activation keys are never stored. sms_reader_keys.key_hash holds
-- an HMAC-SHA256 digest keyed by the server-side SMS key pepper. Device
-- credentials are stored only as HMAC-SHA256 digests.
-- ============================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- Session audience isolation
--
-- The license console and the SMS Reader console share the administrator
-- account table and the session table, but a session minted by one surface
-- must not be accepted by the other. Existing sessions are backfilled as
-- ``keygen``; the SMS application uses ``sms_reader``.
-- ----------------------------------------------------------------------------
ALTER TABLE admin_sessions
    ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'keygen';

COMMENT ON COLUMN admin_sessions.audience IS
    'Application surface that issued the session: keygen or sms_reader.';

CREATE INDEX IF NOT EXISTS ix_admin_sessions_audience
    ON admin_sessions (audience, token_hash);

-- ----------------------------------------------------------------------------
-- One-time SMS reader activation keys
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sms_reader_keys (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash           text NOT NULL,
    key_hint           text NOT NULL DEFAULT '',
    reader_name        text NOT NULL DEFAULT '',
    status             text NOT NULL DEFAULT 'generated',
    batch_id           uuid,
    created_by         uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    activated_at       timestamptz,
    revoked_at         timestamptz,
    revoked_by         uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    revoked_reason     text,
    device_id          text,
    device_token_hash  text,
    last_seen_at       timestamptz,
    CONSTRAINT sms_reader_keys_status_check
        CHECK (status IN ('generated', 'activated', 'revoked')),
    CONSTRAINT sms_reader_keys_hash_check
        CHECK (char_length(key_hash) = 64),
    CONSTRAINT sms_reader_keys_token_hash_check
        CHECK (device_token_hash IS NULL OR char_length(device_token_hash) = 64),
    CONSTRAINT sms_reader_keys_reader_name_check
        CHECK (char_length(reader_name) <= 120),
    CONSTRAINT sms_reader_keys_device_id_check
        CHECK (device_id IS NULL OR char_length(device_id) BETWEEN 4 AND 200),
    CONSTRAINT sms_reader_keys_revoke_reason_check
        CHECK (revoked_reason IS NULL OR char_length(revoked_reason) <= 500),
    -- A generated key is unbound. An activated key is bound to exactly one
    -- device and has a credential digest. A revoked key is terminal and its
    -- credential digest is cleared immediately.
    CONSTRAINT sms_reader_keys_state_check
        CHECK (
            (
                status = 'generated'
                AND activated_at IS NULL
                AND revoked_at IS NULL
                AND device_id IS NULL
                AND device_token_hash IS NULL
            )
            OR (
                status = 'activated'
                AND activated_at IS NOT NULL
                AND revoked_at IS NULL
                AND device_id IS NOT NULL
                AND device_token_hash IS NOT NULL
            )
            OR (
                status = 'revoked'
                AND revoked_at IS NOT NULL
                AND device_token_hash IS NULL
            )
        )
);

COMMENT ON TABLE sms_reader_keys IS
    'One-time SMS reader activation keys. Only HMAC digests and masked hints are stored.';
COMMENT ON COLUMN sms_reader_keys.key_hash IS
    'HMAC-SHA256(server-side SMS pepper, domain-separated normalized activation key).';
COMMENT ON COLUMN sms_reader_keys.key_hint IS
    'Masked display form, e.g. NEXORA-****-****-****-8VL3.';
COMMENT ON COLUMN sms_reader_keys.device_token_hash IS
    'HMAC-SHA256 of the high-entropy device credential; cleared on revocation.';
COMMENT ON COLUMN sms_reader_keys.device_id IS
    'Reader-supplied device identifier bound to this key. Raw value is retained for admin operations.';

-- ----------------------------------------------------------------------------
-- Activation and device authentication history
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sms_reader_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_id      uuid NOT NULL REFERENCES sms_reader_keys(id) ON DELETE CASCADE,
    event_type  text NOT NULL,
    outcome     text NOT NULL DEFAULT 'success',
    device_id   text,
    ip_hash     text,
    user_agent  text,
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sms_reader_events_type_check
        CHECK (event_type IN (
            'generated', 'redeemed', 'authenticated', 'heartbeat',
            'token_rotated', 'revoked', 'delete_blocked'
        )),
    CONSTRAINT sms_reader_events_outcome_check
        CHECK (outcome IN ('success', 'failure')),
    CONSTRAINT sms_reader_events_ip_hash_check
        CHECK (ip_hash IS NULL OR char_length(ip_hash) = 64)
);

COMMENT ON TABLE sms_reader_events IS
    'Reader activation, authentication, heartbeat and credential lifecycle history.';

-- ----------------------------------------------------------------------------
-- Uniqueness: key digests, device credentials, and one active device binding
-- ----------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_sms_reader_keys_key_hash
    ON sms_reader_keys (key_hash);

CREATE UNIQUE INDEX IF NOT EXISTS ux_sms_reader_keys_device_token_hash
    ON sms_reader_keys (device_token_hash)
    WHERE device_token_hash IS NOT NULL;

-- A device may hold only one currently-active reader credential. Revoking the
-- key releases the binding so the administrator can issue a replacement.
CREATE UNIQUE INDEX IF NOT EXISTS ux_sms_reader_keys_active_device
    ON sms_reader_keys (device_id)
    WHERE status = 'activated';

-- ----------------------------------------------------------------------------
-- Query indexes
-- ----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_status_created
    ON sms_reader_keys (status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_created_at
    ON sms_reader_keys (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_activated_at
    ON sms_reader_keys (activated_at DESC)
    WHERE activated_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_reader_lower
    ON sms_reader_keys (lower(reader_name));

CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_device
    ON sms_reader_keys (device_id)
    WHERE device_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_sms_reader_keys_batch
    ON sms_reader_keys (batch_id)
    WHERE batch_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_sms_reader_events_key_created
    ON sms_reader_events (key_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_sms_reader_events_type_created
    ON sms_reader_events (event_type, created_at DESC);

-- ----------------------------------------------------------------------------
-- Row-level security: deny by default for every browser-facing Supabase role.
-- The backend connects as the trusted database role; anon/authenticated get no
-- policies and no table privileges. The service-role credential, when used,
-- stays server-side and bypasses RLS by design.
-- ----------------------------------------------------------------------------
DO $sms_rls$
DECLARE
    tbl text;
    has_anon boolean;
    has_authenticated boolean;
    has_service_role boolean;
BEGIN
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') INTO has_anon;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') INTO has_authenticated;
    SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') INTO has_service_role;

    FOREACH tbl IN ARRAY ARRAY['sms_reader_keys', 'sms_reader_events']
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
$sms_rls$;

COMMIT;
