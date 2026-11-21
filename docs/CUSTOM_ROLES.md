# Custom Platform Roles

Status: implemented and tested against the local PostgreSQL test database.
Migration 011 has not been applied to production.

## Model

Built-in roles remain code-defined in `ROLE_PERMISSIONS` and are immutable:
Owner, Administrator, Project manager, Key manager, and Read only. Custom roles
are named permission sets stored in `nexora_roles`. An administrator can be
assigned a custom role through `admin_users.platform_role_id`.

Platform roles remain the base authority. Project-scoped roles are additional
grants stored in `nexora_project_role_assignments`; they never change
`admin_users.platform_role`. Authorization is evaluated server-side on every
request from the database; hiding a button in the browser is never an
authorization control.

## API

| Method | Endpoint | Permission |
|---|---|---|
| `GET` | `/api/v1/universal/roles` | `admin:read` |
| `POST` | `/api/v1/universal/roles` | `admin:roles` |
| `PATCH` | `/api/v1/universal/roles/<id>` | `admin:roles` |
| `POST` | `/api/v1/universal/roles/<id>/clone` | `admin:roles` |
| `DELETE` | `/api/v1/universal/roles/<id>` | `admin:roles` |
| `GET` | `/api/v1/universal/projects/<id>/roles` | `admin:read` |
| `GET` | `/api/v1/universal/projects/<id>/role-assignments` | `admin:read` |
| `POST` | `/api/v1/universal/projects/<id>/role-assignments` | `admin:roles` |
| `DELETE` | `/api/v1/universal/projects/<id>/role-assignments/<assignment_id>` | `admin:roles` |
| `PATCH` | `/api/v1/universal/admins/<id>/role` | `admin:roles` |
| `POST` | `/api/v1/universal/admins/<id>/sessions/revoke` | `admin:sessions` |

Create body:

```json
{
  "id": "ops_reader",
  "name": "Ops Reader",
  "description": "Read-only operations role.",
  "permissions": ["analytics:read", "project:read", "key:read"]
}
```

`id` is optional (derived from the name) and must match
`^[a-z][a-z0-9_]{2,49}$`. Names are 1–80 characters; descriptions are at most
500 characters. A role must contain at least one known permission and at most 64.
The `template` field can seed permissions from a built-in template when the
permissions array is empty.

Assignment body:

```json
{ "platform_role": "custom", "platform_role_id": "ops_reader" }
```

Use a built-in role id with `platform_role` for a built-in assignment. The role
list returns the permission catalogue, built-in roles, custom roles, assignment
counts, and templates.

## Security invariants

- Only known platform permissions can be stored.
- A non-Owner can grant only permissions the actor already holds.
- A non-Owner cannot grant `critical` risk permissions.
- Only an Owner can assign the Owner role.
- The last active Owner cannot be demoted.
- A role that is assigned to any administrator cannot be deleted.
- Built-in role ids cannot be reused or modified.
- The built-in Owner role cannot be cloned.
- Create, update, clone, delete, and assignment are transactional and audited.
- The role is reloaded on each request, so a permission change takes effect
  without trusting a stale frontend session.

Role mutation audits use actions such as `platform_role.create`,
`platform_role.update`, `platform_role.clone`, `platform_role.delete`, and the
administrator role-change audit event. Audit details contain the role id, name,
and permission set, never credentials.

## UI

The platform Roles view provides:

- built-in and custom role inventory with assignment counts;
- a permission matrix;
- create/edit forms with permission checkboxes and risk labels;
- template selection;
- clone and delete actions;
- server error display for escalation and last-owner refusals.

The Administrators view lists built-in and custom roles and applies either a
built-in platform role or `{platform_role: "custom", platform_role_id: ...}`.

## Verification

```bash
python -m pytest tests/test_project_transfer_roles_scheduler.py -q
```

The automated cases cover custom role creation, listing, update, clone,
assignment, server-side enforcement after a fresh login, escalation refusal,
last-owner protection, and role deletion guards.

## Project-scoped roles

Project roles are created with `project_id` set and use only the
`PROJECT_ROLE_PERMISSIONS` subset. They cannot grant global-only permissions or
Owner authority. Because `admin:roles` is not in that subset, creating and
assigning roles remains a global capability: the actor needs the global
`admin:roles` permission, and assignment/removal is scoped to the target
project. A project role cannot be deleted while assigned. Project roles never
change `admin_users.platform_role`, so the global last-Owner protection still
applies and a project-role removal cannot demote or delete the global Owner.

## Production notes

Apply migration 011 through the approved migration process. Existing built-in
roles are unaffected. Review custom assignments after restore/import, since
project bundles do not contain administrator accounts or role assignments.
