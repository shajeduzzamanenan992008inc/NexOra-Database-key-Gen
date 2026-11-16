-- =============================================================================
-- Migration 006: Rename rh_set_updated_at → nexora_set_updated_at
-- =============================================================================
-- Purpose: Remove legacy "rh-esports" branding from database functions.
--          The trigger function rh_set_updated_at() was created in migration 001
--          under the old project name. This migration creates a properly-named
--          replacement and updates all triggers to use it.
--
-- IMPORTANT: This migration does NOT change the HMAC domain separator string
--            "_DOMAIN_PREFIX = 'rh-esports:v1'" inside backend/security/keys.py.
--            Changing that value would invalidate all existing stored key digests.
--            The domain prefix is a cryptographic constant and must remain stable.
--
-- Safety notes:
--   - CREATE OR REPLACE: safe to run on a DB that already has the old function.
--   - DROP TRIGGER IF EXISTS: safe if trigger doesn't exist yet.
--   - CREATE TRIGGER ... IF NOT EXISTS: safe on repeated runs (PG 14+).
--   - The old function rh_set_updated_at is dropped last so it cannot be called
--     by any trigger after this migration completes.
--   - Rollback: To undo, recreate rh_set_updated_at, repoint triggers, drop
--     nexora_set_updated_at. See rollback section at the bottom.
--
-- Tables affected: admin_users, license_keys
-- =============================================================================

BEGIN;

-- ── Step 1: Create the new trigger function ──────────────────────────────────
CREATE OR REPLACE FUNCTION nexora_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION nexora_set_updated_at() IS
  'Sets updated_at to NOW() on every UPDATE. Replaces rh_set_updated_at (migration 006).';

-- ── Step 2: Repoint triggers on admin_users ──────────────────────────────────
DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;

CREATE TRIGGER trg_admin_users_updated_at
  BEFORE UPDATE ON admin_users
  FOR EACH ROW
  EXECUTE FUNCTION nexora_set_updated_at();

-- ── Step 3: Repoint triggers on license_keys ─────────────────────────────────
DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;

CREATE TRIGGER trg_license_keys_updated_at
  BEFORE UPDATE ON license_keys
  FOR EACH ROW
  EXECUTE FUNCTION nexora_set_updated_at();

-- ── Step 4: Repoint triggers on nexora_projects (if trigger exists) ───────────
-- migration 005 may have created triggers on nexora_projects; if it already
-- used nexora_set_updated_at this is a no-op. If it used rh_set_updated_at,
-- the DROP + CREATE below fixes it.
DROP TRIGGER IF EXISTS trg_nexora_projects_updated_at ON nexora_projects;

CREATE TRIGGER trg_nexora_projects_updated_at
  BEFORE UPDATE ON nexora_projects
  FOR EACH ROW
  EXECUTE FUNCTION nexora_set_updated_at();

-- ── Step 5: Drop the old function ────────────────────────────────────────────
-- CASCADE is intentionally omitted: by this point no trigger depends on it.
-- If a dependency remains the DROP will fail loudly, surfacing the problem.
DROP FUNCTION IF EXISTS rh_set_updated_at();

COMMIT;

-- =============================================================================
-- ROLLBACK SCRIPT (do not execute unless rolling back migration 006)
-- =============================================================================
-- BEGIN;
--
-- CREATE OR REPLACE FUNCTION rh_set_updated_at()
-- RETURNS TRIGGER LANGUAGE plpgsql AS $$
-- BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$;
--
-- DROP TRIGGER IF EXISTS trg_admin_users_updated_at ON admin_users;
-- CREATE TRIGGER trg_admin_users_updated_at
--   BEFORE UPDATE ON admin_users FOR EACH ROW
--   EXECUTE FUNCTION rh_set_updated_at();
--
-- DROP TRIGGER IF EXISTS trg_license_keys_updated_at ON license_keys;
-- CREATE TRIGGER trg_license_keys_updated_at
--   BEFORE UPDATE ON license_keys FOR EACH ROW
--   EXECUTE FUNCTION rh_set_updated_at();
--
-- DROP TRIGGER IF EXISTS trg_nexora_projects_updated_at ON nexora_projects;
-- CREATE TRIGGER trg_nexora_projects_updated_at
--   BEFORE UPDATE ON nexora_projects FOR EACH ROW
--   EXECUTE FUNCTION rh_set_updated_at();
--
-- DROP FUNCTION IF EXISTS nexora_set_updated_at();
--
-- COMMIT;
-- =============================================================================
