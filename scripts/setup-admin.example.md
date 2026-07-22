# First administrator setup

The setup script wraps the idempotent AUTH-09 operations. It resolves an
existing application identity by provider subject, previews the tenant and
RBAC changes, and only applies them after confirmation.

It never accepts OAuth tokens, API keys, or provider credentials. Sign in once
through Google or Microsoft before running it so an application identity
exists.

## Local

```bash
sudo -u baonghia ./scripts/setup-admin.sh
```

The local defaults are:

- project: the repository containing the script
- environment: `.env.local`, falling back to `.env`
- virtualenv: `apps/api/.venv`, falling back to `.venv`

## Production

```bash
sudo -u desify ./scripts/setup-admin.sh --environment production
```

The production defaults are:

- project: `/opt/creative-asset-manager/current`
- environment: `/etc/creative-asset-manager/production.env`
- virtualenv: `/opt/creative-asset-manager/venv`

## Non-interactive production example

```bash
sudo -u desify ./scripts/setup-admin.sh \
  --environment production \
  --provider google \
  --subject "GOOGLE_SUBJECT" \
  --tenant-slug "desify" \
  --tenant-name "Desify" \
  --reason "Initial production administrator" \
  --yes
```

To grant durable platform administration as a separate explicit operation,
add `--platform-admin`. This is never granted by default.

## Safety and reruns

Before changing data, the script verifies the database connection, the single
Alembic head, persistent RBAC authentication, and production compatibility
controls. It lists only masked emails and shortened subjects, performs a
mandatory dry-run, then verifies the active membership, `tenant_admin` role,
`ai_operations.read`, and `tenant_members.manage`.

Rerunning with the same provider subject and tenant is safe: tenant,
membership, role assignment, and platform assignment are reused. The script
does not modify `PROCESSING_POLICY_ADMIN_IDS` or enable legacy authorization.

After success, log out and sign in again so the browser receives a fresh
application session and authorization snapshot.
