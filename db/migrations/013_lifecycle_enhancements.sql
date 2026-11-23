-- ============================================================================
-- NexOra OMEGA — Migration 013
-- Lifecycle Enhancements: retention config only
-- Target: PostgreSQL 13+ / Supabase
-- Forward-only. Migrations 001–012 are unchanged and must never be edited.
-- This migration is additive and idempotent. Only adds system_settings entries
-- and key_type columns when absent. Does not touch schema_migrations tracking.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Add retention configuration to system_settings (idempotent, default 0)
-- ----------------------------------------------------------------------------

-- Key retention: days to retain keys in pending_deletion before permanent deletion
INSERT INTO system_settings (key, value, description, is_secret)
VALUES (
    'nexora.key_lifecycle_retention_days',
    '0',
    'Number of days to retain keys in pending_deletion state before permanent deletion. Default 0 = immediate hard deletion after pending_deletion, for transaction-safe cleanup.',
    false
)
ON CONFLICT (key) DO NOTHING;

-- Project retention: days to retain projects in pending_deletion before permanent deletion
INSERT INTO system_settings (key, value, description, is_secret)
VALUES (
    'nexora.project_lifecycle_retention_days',
    '0',
    'Number of days to retain projects in pending_deletion state before permanent deletion. Default 0 = immediate hard deletion after pending_deletion.',
    false
)
ON CONFLICT (key) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 2. Add deleted_reason audit trail for key types (idempotent)
-- ----------------------------------------------------------------------------

ALTER TABLE nexora_key_types
    ADD COLUMN IF NOT EXISTS deleted_reason text NOT NULL DEFAULT '';

-- ----------------------------------------------------------------------------
-- 3. No schema_migrations tracking; purely additive changes
-- ----------------------------------------------------------------------------

COMMIT;