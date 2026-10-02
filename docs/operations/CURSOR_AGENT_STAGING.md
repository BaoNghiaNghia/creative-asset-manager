# Cursor Agent + My Machines staging workflow

## Purpose

This is the autonomous development lane for Creative Asset Manager.

- Cursor Agent handles code inspection, edits, tests, local/staging execution, and Browser verification.
- Cursor Project Rules keep the agent inside the development/staging boundary.
- My Machines runs Cursor tool calls on a dedicated VPS checkout.
- CodeLocal remains the production deployment gate and the native Windows orchestration path.

The worker must not run from the production checkout or as root.

## Topology

```text
Cursor Agent / Cursor Automations
          |
          v
My Machines: cam-vps-staging
          |
          v
/srv/creative-asset-manager-cursor
  - task branches only
  - build/test/integration
  - Browser verification
  - no production secrets
  - no sudo
          |
          v
ready handoff
          |
          +--> CodeLocal on creativeasset -> reviewed production web/API deploy
          |
          +--> CodeLocal on BaoNghia -> native Windows desktop build/publish
```

Production remains at the existing native release paths documented in
`docs/operations/VPS_DEPLOYMENT.md`.

## Repository controls

Cursor automatically reads:

- `AGENTS.md`
- `.cursor/rules/cam-safe-delivery.mdc`
- `.cursor/rules/cam-ui-design.mdc` for frontend/UI design work

For screenshot-driven UI work, also follow `docs/operations/CURSOR_UI_DESIGN.md`.

Before a task is ready, run this from a task branch (the gate rejects `main` by default):

```bash
bash scripts/cam-cursor-staging-gate.sh
```

For security-sensitive, migration, storage, provider, pipeline, infrastructure,
or cross-cutting work:

```bash
CAM_STAGING_FULL=1 bash scripts/cam-cursor-staging-gate.sh
```

UI changes also require Browser verification against loopback or an explicitly
designated staging URL. Browser must not perform mutating production actions.

## Bootstrap My Machines on the VPS

Run from the production source checkout as an operator:

```bash
sudo bash scripts/cam-bootstrap-cursor-my-machine.sh
```

The bootstrap:

1. creates the unprivileged `cursoragent` account;
2. creates an isolated checkout at `/srv/creative-asset-manager-cursor`;
3. installs the official Cursor CLI for that user;
4. installs the hardened systemd unit;
5. deliberately does not automate Cursor account authentication.

Then authenticate once:

```bash
sudo -u cursoragent -H /var/lib/cursoragent/.local/bin/agent login
sudo -u cursoragent -H /var/lib/cursoragent/.local/bin/agent worker debug
```

When debug is healthy:

```bash
sudo systemctl enable --now cam-cursor-my-machine.service
sudo systemctl status cam-cursor-my-machine.service --no-pager
```

The worker is named `cam-vps-staging`.

## Normal task flow

1. Trigger an Agent on `cam-vps-staging`.
2. Agent creates/reuses a task branch; never develops directly on `main`.
3. Agent inspects relevant docs and code, implements the smallest change, and runs the staging gate.
4. For UI work, Agent starts the required local service and uses Browser to inspect the changed flow, console, and failed requests.
5. Agent reports branch/commit, changed behavior, tests, Browser evidence, migration/dependency/secret impact, risks, and rollback notes.
6. Only after an explicit current user instruction does CodeLocal merge/push/deploy production.
7. Desktop releases are built only on the authorized BaoNghia Windows workspace and then published to the existing update feed.

## Automation

After the My Machines worker is online, Cursor Automations can target it for
scheduled/event-driven development work. Use the machine name
`cam-vps-staging` and keep automation prompts scoped to development/staging.

Good automation candidates:

- PR review/fix/test loops;
- scheduled dependency/test maintenance;
- bug triage from trusted issue sources;
- UI regression checks on staging;
- periodic repository health checks.

Do not create an automation that deploys production, edits production secrets,
runs destructive migrations, or publishes the Windows release.

## Production handoff

A ready Cursor task is not a production deployment. The production gate remains:

```text
Cursor ready -> CodeLocal review/verify -> explicit user production instruction
             -> production deploy -> /live + /ready + /version verification
```

For Windows:

```text
Cursor ready -> CodeLocal review -> BaoNghia native Windows build
             -> verify installer/update files -> publish update feed
```

This keeps day-to-day implementation highly automatic without giving the
development agent permanent production authority.
