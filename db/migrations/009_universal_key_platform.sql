-- ============================================================================
-- NexOra OMEGA — Migration 009: Universal Key Management Platform
-- Target: PostgreSQL 13+ / Supabase
--
-- Forward-only. Migrations 001–008 are unchanged.
--
-- Adds project metadata/expiration, key types, extended key metadata,
-- declarative custom key types, devices, key events, API usage, webhooks,
-- notifications, automation runs, admin platform roles, and audit correlation.
-- Existing Reader Key and SMS tables are preserved.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Project metadata, status and expiration
-- ----------------------------------------------------------------------------
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS project_type text NOT NULL DEFAULT 'database';
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS never_expires boolean NOT NULL DEFAULT true;
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS settings jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS archived_at timestamptz;
ALTER TABLE nexora_projects
    ADD COLUMN IF NOT EXISTS cloned_from uuid REFERENCES nexora_projects(id) ON DELETE SET NULL;

UPDATE nexora_projects
   SET never_expires = true, expires_at = NULL
 WHERE expires_at IS NULL AND never_expires = false;

ALTER TABLE nexora_projects
    DROP CONSTRAINT IF EXISTS nexora_projects_status_check;
ALTER TABLE nexora_projects
    ADD CONSTRAINT nexora_projects_status_check
    CHECK (status IN ('active', 'inactive', 'expired', 'deleted'));

ALTER TABLE nexora_projects
    DROP CONSTRAINT IF EXISTS nexora_projects_type_length_check;
ALTER TABLE nexora_projects
    ADD CONSTRAINT nexora_projects_type_length_check
    CHECK (char_length(project_type) BETWEEN 1 AND 40);

CREATE INDEX IF NOT EXISTS ix_nexora_projects_expires
    ON nexora_projects (expires_at)
    WHERE expires_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_nexora_projects_archived
    ON nexora_projects (archived_at)
    WHERE archived_at IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_nexora_projects_type
    ON nexora_projects (project_type, status);

-- ----------------------------------------------------------------------------
-- 2. Declarative permission catalogue
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_permission_definitions (
    id            text PRIMARY KEY,
    name          text NOT NULL,
    description   text NOT NULL DEFAULT '',
    risk          text NOT NULL DEFAULT 'standard',
    is_system     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_permission_id_check
        CHECK (id ~ '^[a-z][a-z0-9_]{1,49}$'),
    CONSTRAINT nexora_permission_risk_check
        CHECK (risk IN ('standard', 'elevated', 'critical'))
);

INSERT INTO nexora_permission_definitions (id, name, description, risk)
VALUES
    ('read', 'Read', 'Read project or resource metadata.', 'standard'),
    ('write', 'Write', 'Create resources or append data.', 'elevated'),
    ('update', 'Update', 'Modify existing resources.', 'elevated'),
    ('delete', 'Delete', 'Delete resources owned by the key scope.', 'critical'),
    ('api_access', 'API Access', 'Call versioned NexOra APIs.', 'elevated'),
    ('device_access', 'Device Access', 'Bind or authenticate a device.', 'elevated'),
    ('feature_access', 'Feature Access', 'Use a licensed product feature.', 'standard'),
    ('database_operation', 'Database Operation', 'Request permitted database operations.', 'critical'),
    ('admin_operation', 'Admin Operation', 'Perform administrative operations.', 'critical'),
    ('custom', 'Custom Scope', 'A project-defined declarative scope.', 'elevated')
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 3. Key type catalogue (built-in + project custom types)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_key_types (
    id                    text PRIMARY KEY,
    name                  text NOT NULL,
    description           text NOT NULL DEFAULT '',
    project_id            uuid REFERENCES nexora_projects(id) ON DELETE CASCADE,
    key_prefix            text NOT NULL DEFAULT 'NX',
    key_format            jsonb NOT NULL DEFAULT '{"groups":4,"group_length":4,"separator":"-"}'::jsonb,
    permissions           jsonb NOT NULL DEFAULT '["read"]'::jsonb,
    scopes                jsonb NOT NULL DEFAULT '[]'::jsonb,
    expiration_policy     jsonb NOT NULL DEFAULT '{"presets":["1_day","7_days","30_days","90_days","1_year","custom","never"],"default":"never"}'::jsonb,
    device_policy         jsonb NOT NULL DEFAULT '{"binds":false,"max_devices":0,"required":false}'::jsonb,
    usage_limits          jsonb NOT NULL DEFAULT '{"rate_per_minute":60,"max_usage":null}'::jsonb,
    lifecycle_rules       jsonb NOT NULL DEFAULT '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}'::jsonb,
    endpoint_permissions  jsonb NOT NULL DEFAULT '[]'::jsonb,
    validation_rules      jsonb NOT NULL DEFAULT '{}'::jsonb,
    metadata_schema       jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_builtin            boolean NOT NULL DEFAULT false,
    is_active             boolean NOT NULL DEFAULT true,
    -- No foreign key here: key types are durable global configuration and must
    -- not be destroyed by a CASCADE truncate of administrator test fixtures.
    created_by            uuid,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_key_types_id_check
        CHECK (id ~ '^[a-z][a-z0-9_]{2,49}$'),
    CONSTRAINT nexora_key_types_name_check
        CHECK (char_length(name) BETWEEN 1 AND 80),
    CONSTRAINT nexora_key_types_prefix_check
        CHECK (key_prefix ~ '^[A-Z0-9]{0,12}$'),
    CONSTRAINT nexora_key_types_permissions_array
        CHECK (jsonb_typeof(permissions) = 'array'),
    CONSTRAINT nexora_key_types_scopes_array
        CHECK (jsonb_typeof(scopes) = 'array'),
    CONSTRAINT nexora_key_types_format_object
        CHECK (jsonb_typeof(key_format) = 'object'),
    CONSTRAINT nexora_key_types_validation_object
        CHECK (jsonb_typeof(validation_rules) = 'object'),
    CONSTRAINT nexora_key_types_metadata_object
        CHECK (jsonb_typeof(metadata_schema) = 'object')
);

CREATE INDEX IF NOT EXISTS ix_nexora_key_types_project
    ON nexora_key_types (project_id, is_active);
CREATE INDEX IF NOT EXISTS ix_nexora_key_types_builtin
    ON nexora_key_types (is_builtin, is_active);

INSERT INTO nexora_key_types
    (id, name, description, key_prefix, permissions, device_policy, usage_limits,
     lifecycle_rules, is_builtin)
VALUES
    ('reader', 'Reader Key', 'One-time Reader App activation key.', 'RDR',
     '["read","device_access"]', '{"binds":true,"max_devices":1,"required":true}',
     '{"rate_per_minute":60,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":false}', true),
    ('license', 'License Key', 'Activates a licensed product feature.', 'LIC',
     '["read","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('api', 'API Key', 'Server-to-server API access credential.', 'API',
     '["read","write","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":600,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('admin', 'Admin Key', 'Elevated operator credential; never bypasses sessions.', 'ADM',
     '["read","api_access","admin_operation"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":30,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('service', 'Service Key', 'Scoped machine-to-machine service credential.', 'SVC',
     '["read","write","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":300,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('webhook', 'Webhook Key', 'Signs or authenticates webhook deliveries.', 'WHK',
     '["api_access","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('user', 'User Key', 'Scoped credential for a human end user.', 'USR',
     '["read","write"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('device', 'Device Key', 'Credential bound to one or more devices.', 'DEV',
     '["read","device_access"]', '{"binds":true,"max_devices":3,"required":true}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('temporary', 'Temporary Key', 'Short-lived scoped access key.', 'TMP',
     '["read"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":60,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('custom', 'Custom Key', 'Base type for project-defined credentials.', 'CUS',
     '["read"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":60,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('oauth_client', 'OAuth Client Key', 'Future-compatible OAuth client credential.', 'OAU',
     '["api_access","read"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('enterprise', 'Enterprise Key', 'Enterprise-scoped access credential.', 'ENT',
     '["read","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":300,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('trial', 'Trial Key', 'Time-limited trial access key.', 'TRI',
     '["read","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":60,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('subscription', 'Subscription Key', 'Subscription entitlement credential.', 'SUB',
     '["read","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('sandbox', 'Sandbox Key', 'Non-production sandbox credential.', 'SBX',
     '["read","write","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":300,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('production', 'Production Key', 'Production-scoped credential.', 'PRD',
     '["read","write","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":600,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('developer', 'Developer Key', 'Developer workspace credential.', 'DVL',
     '["read","write","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":300,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('partner', 'Partner Key', 'Partner integration credential.', 'PTR',
     '["read","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":300,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('access_token', 'Access Token', 'Short-lived API access token.', 'ATK',
     '["read","api_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":600,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true),
    ('ai_service', 'AI Service Key', 'Scoped credential for an AI service.', 'AIK',
     '["read","api_access","feature_access"]', '{"binds":false,"max_devices":0,"required":false}',
     '{"rate_per_minute":120,"max_usage":null}', '{"can_activate":true,"can_deactivate":true,"can_regenerate":true}', true)
ON CONFLICT (id) DO NOTHING;

-- ----------------------------------------------------------------------------
-- 4. Extend the existing key table (single source of truth for all key types)
-- ----------------------------------------------------------------------------
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS key_type_id text NOT NULL DEFAULT 'reader'
        REFERENCES nexora_key_types(id) ON DELETE RESTRICT;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS fingerprint text;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS permissions jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS scopes jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS tags text[] NOT NULL DEFAULT '{}'::text[];
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS notes text NOT NULL DEFAULT '';
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS usage_count integer NOT NULL DEFAULT 0;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS max_usage integer;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS activated_at timestamptz;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS deactivated_at timestamptz;
ALTER TABLE reader_keys
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

UPDATE reader_keys SET key_type_id = 'reader' WHERE key_type_id IS NULL;

ALTER TABLE reader_keys
    DROP CONSTRAINT IF EXISTS reader_keys_status_check;
ALTER TABLE reader_keys
    ADD CONSTRAINT reader_keys_status_check
    CHECK (status IN ('generated', 'active', 'used', 'deactivated', 'expired', 'revoked', 'deleted'));

ALTER TABLE reader_keys
    DROP CONSTRAINT IF EXISTS reader_keys_state_check;
ALTER TABLE reader_keys
    ADD CONSTRAINT reader_keys_state_check
    CHECK (
        (status = 'revoked' AND revoked_at IS NOT NULL)
        OR (status = 'used' AND used_at IS NOT NULL AND revoked_at IS NULL)
        OR (status IN ('generated', 'active', 'deactivated', 'expired', 'deleted')
            AND revoked_at IS NULL)
    );

ALTER TABLE reader_keys
    DROP CONSTRAINT IF EXISTS reader_keys_usage_check;
ALTER TABLE reader_keys
    ADD CONSTRAINT reader_keys_usage_check
    CHECK (usage_count >= 0 AND (max_usage IS NULL OR max_usage > 0));

CREATE UNIQUE INDEX IF NOT EXISTS ux_reader_keys_fingerprint
    ON reader_keys (fingerprint)
    WHERE fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_reader_keys_type_status
    ON reader_keys (key_type_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reader_keys_tags
    ON reader_keys USING gin (tags);
CREATE INDEX IF NOT EXISTS ix_reader_keys_permissions
    ON reader_keys USING gin (permissions);

-- ----------------------------------------------------------------------------
-- 5. Key lifecycle history (survives hard revocation/project deletion)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_key_events (
    id            bigserial PRIMARY KEY,
    project_id    uuid REFERENCES nexora_projects(id) ON DELETE SET NULL,
    key_id        uuid REFERENCES reader_keys(id) ON DELETE SET NULL,
    fingerprint   text NOT NULL DEFAULT '',
    event         text NOT NULL,
    actor_id      uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    actor_email   text NOT NULL DEFAULT '',
    result        text NOT NULL DEFAULT 'success',
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_nexora_key_events_key
    ON nexora_key_events (key_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_key_events_project
    ON nexora_key_events (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_key_events_fingerprint
    ON nexora_key_events (fingerprint, created_at DESC);

-- ----------------------------------------------------------------------------
-- 6. Universal device registry (separate from Reader App installations)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_devices (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id        uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE CASCADE,
    key_id            uuid REFERENCES reader_keys(id) ON DELETE SET NULL,
    installation_id   text NOT NULL DEFAULT '',
    device_id         text NOT NULL,
    device_name       text NOT NULL DEFAULT '',
    device_model      text NOT NULL DEFAULT '',
    platform          text NOT NULL DEFAULT '',
    status            text NOT NULL DEFAULT 'active',
    credential_hash   text NOT NULL DEFAULT '',
    metadata          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    last_seen_at      timestamptz NOT NULL DEFAULT now(),
    deactivated_at    timestamptz,
    revoked_at        timestamptz,
    CONSTRAINT nexora_devices_status_check
        CHECK (status IN ('active', 'deactivated', 'revoked')),
    CONSTRAINT nexora_devices_device_id_check
        CHECK (char_length(device_id) BETWEEN 1 AND 200),
    CONSTRAINT nexora_devices_credential_check
        CHECK (credential_hash = '' OR char_length(credential_hash) = 64),
    CONSTRAINT nexora_devices_state_check
        CHECK (
            (status = 'active' AND revoked_at IS NULL)
            OR (status = 'deactivated' AND revoked_at IS NULL)
            OR (status = 'revoked' AND revoked_at IS NOT NULL)
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nexora_devices_project_device
    ON nexora_devices (project_id, device_id);
CREATE INDEX IF NOT EXISTS ix_nexora_devices_key
    ON nexora_devices (key_id, status);
CREATE INDEX IF NOT EXISTS ix_nexora_devices_last_seen
    ON nexora_devices (last_seen_at DESC);

-- ----------------------------------------------------------------------------
-- 7. API usage analytics
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_api_usage (
    id            bigserial PRIMARY KEY,
    project_id    uuid REFERENCES nexora_projects(id) ON DELETE CASCADE,
    key_id        uuid REFERENCES reader_keys(id) ON DELETE SET NULL,
    key_type_id   text NOT NULL DEFAULT '',
    endpoint      text NOT NULL DEFAULT '',
    method        text NOT NULL DEFAULT '',
    status_code   integer NOT NULL DEFAULT 0,
    latency_ms    integer NOT NULL DEFAULT 0,
    ip_hash       text NOT NULL DEFAULT '',
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_nexora_api_usage_project_time
    ON nexora_api_usage (project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_api_usage_key_time
    ON nexora_api_usage (key_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_nexora_api_usage_time
    ON nexora_api_usage (created_at DESC);

-- ----------------------------------------------------------------------------
-- 8. Webhooks and delivery history
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_webhooks (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL REFERENCES nexora_projects(id) ON DELETE CASCADE,
    name            text NOT NULL,
    url             text NOT NULL,
    events          text[] NOT NULL DEFAULT '{}'::text[],
    secret_salt     text NOT NULL,
    secret_version  integer NOT NULL DEFAULT 1,
    is_active       boolean NOT NULL DEFAULT true,
    created_by      uuid REFERENCES admin_users(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_webhooks_name_check
        CHECK (char_length(name) BETWEEN 1 AND 100),
    CONSTRAINT nexora_webhooks_url_check
        CHECK (url ~ '^https?://[^[:space:]]+$'),
    CONSTRAINT nexora_webhooks_events_check
        CHECK (array_length(events, 1) IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS ix_nexora_webhooks_project
    ON nexora_webhooks (project_id, is_active);

CREATE TABLE IF NOT EXISTS nexora_webhook_deliveries (
    id              bigserial PRIMARY KEY,
    webhook_id      uuid NOT NULL REFERENCES nexora_webhooks(id) ON DELETE CASCADE,
    event_id        uuid NOT NULL,
    event_type      text NOT NULL,
    payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'pending',
    attempts        integer NOT NULL DEFAULT 0,
    response_code   integer,
    error           text NOT NULL DEFAULT '',
    next_attempt_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    delivered_at    timestamptz,
    CONSTRAINT nexora_webhook_delivery_status_check
        CHECK (status IN ('pending', 'delivered', 'failed', 'dead')),
    CONSTRAINT nexora_webhook_delivery_attempts_check
        CHECK (attempts >= 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nexora_webhook_delivery_event
    ON nexora_webhook_deliveries (webhook_id, event_id);
CREATE INDEX IF NOT EXISTS ix_nexora_webhook_delivery_queue
    ON nexora_webhook_deliveries (status, next_attempt_at);

-- ----------------------------------------------------------------------------
-- 9. Notification center
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_notifications (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_user_id uuid REFERENCES admin_users(id) ON DELETE CASCADE,
    project_id    uuid REFERENCES nexora_projects(id) ON DELETE CASCADE,
    key_id        uuid REFERENCES reader_keys(id) ON DELETE SET NULL,
    type          text NOT NULL,
    severity      text NOT NULL DEFAULT 'info',
    title         text NOT NULL,
    body          text NOT NULL DEFAULT '',
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    read_at       timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT nexora_notifications_severity_check
        CHECK (severity IN ('info', 'warning', 'critical', 'success'))
);

CREATE INDEX IF NOT EXISTS ix_nexora_notifications_unread
    ON nexora_notifications (created_at DESC)
    WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_nexora_notifications_admin
    ON nexora_notifications (admin_user_id, read_at, created_at DESC);

CREATE TABLE IF NOT EXISTS nexora_notification_preferences (
    admin_user_id uuid PRIMARY KEY REFERENCES admin_users(id) ON DELETE CASCADE,
    preferences   jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 10. Automation run ledger
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nexora_automation_runs (
    id          bigserial PRIMARY KEY,
    job         text NOT NULL,
    status      text NOT NULL DEFAULT 'running',
    details     jsonb NOT NULL DEFAULT '{}'::jsonb,
    error       text NOT NULL DEFAULT '',
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT nexora_automation_status_check
        CHECK (status IN ('running', 'success', 'failed'))
);

CREATE INDEX IF NOT EXISTS ix_nexora_automation_runs_job
    ON nexora_automation_runs (job, started_at DESC);

-- ----------------------------------------------------------------------------
-- 11. Admin platform roles and audit correlation
-- ----------------------------------------------------------------------------
ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS platform_role text;
UPDATE admin_users
   SET platform_role = CASE role
       WHEN 'owner' THEN 'owner'
       WHEN 'admin' THEN 'administrator'
       ELSE 'read_only'
   END
 WHERE platform_role IS NULL;
ALTER TABLE admin_users
    ALTER COLUMN platform_role SET DEFAULT 'read_only';
ALTER TABLE admin_users
    ALTER COLUMN platform_role SET NOT NULL;
ALTER TABLE admin_users
    DROP CONSTRAINT IF EXISTS admin_users_platform_role_check;
ALTER TABLE admin_users
    ADD CONSTRAINT admin_users_platform_role_check
    CHECK (platform_role IN (
        'owner', 'administrator', 'project_manager', 'key_manager',
        'read_only', 'custom'
    ));
ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS platform_permissions jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Keep new administrators aligned with their base role. Platform roles can be
-- changed afterwards only through the owner-only role endpoint.
CREATE OR REPLACE FUNCTION public.nexora_admin_platform_role_default()
RETURNS trigger
LANGUAGE plpgsql
AS $nexora_platform_role$
BEGIN
    IF NEW.platform_role IS NULL OR NEW.platform_role = '' THEN
        NEW.platform_role := CASE NEW.role
            WHEN 'owner' THEN 'owner'
            WHEN 'admin' THEN 'administrator'
            ELSE 'read_only'
        END;
    END IF;
    RETURN NEW;
END
$nexora_platform_role$;

DROP TRIGGER IF EXISTS trg_nexora_admin_platform_role ON admin_users;
CREATE TRIGGER trg_nexora_admin_platform_role
    BEFORE INSERT ON admin_users
    FOR EACH ROW
    EXECUTE FUNCTION public.nexora_admin_platform_role_default();

ALTER TABLE admin_audit_logs
    ADD COLUMN IF NOT EXISTS request_id text;
CREATE INDEX IF NOT EXISTS ix_admin_audit_logs_request
    ON admin_audit_logs (request_id)
    WHERE request_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- 12. Deny-by-default RLS for browser-facing roles
-- ----------------------------------------------------------------------------
DO $nexora_universal_rls$
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
        'nexora_permission_definitions', 'nexora_key_types', 'nexora_key_events',
        'nexora_devices', 'nexora_api_usage', 'nexora_webhooks',
        'nexora_webhook_deliveries', 'nexora_notifications',
        'nexora_notification_preferences', 'nexora_automation_runs'
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
$nexora_universal_rls$;

COMMIT;
