-- ============================================================================
-- RH E-SPORTS — Secure Key Generator & License Management System
-- Migration 003: document the password hashing algorithm
--
-- Existing hashes are not modified. Legacy scrypt hashes remain verifiable by
-- the application, but every new or changed password is Argon2id.
-- ============================================================================

BEGIN;

COMMENT ON TABLE admin_users IS
    'Administrator accounts. Passwords are Argon2id hashes (legacy scrypt hashes remain verifiable); roles are enforced server-side.';

COMMENT ON COLUMN admin_users.password_hash IS
    'Argon2id password hash. Legacy scrypt hashes are accepted for verification only and are replaced on the next explicit password change.';

COMMIT;
