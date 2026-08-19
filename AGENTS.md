# Repository instructions

## Database backup work

Before planning, implementing, modifying, reviewing, or deploying database backup functionality, read:

- [docs/operations/DATABASE_BACKUP.md](docs/operations/DATABASE_BACKUP.md)
- [docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md](docs/plans/DATABASE_BACKUP_IMPLEMENTATION.md)

Those documents are the repository source of truth for database-backup requirements unless the current user explicitly overrides them. Their existence does not authorize production backup actions.

When implementing backup work, start from current `main` on a feature branch, inspect the actual current code before editing, and reuse managed Google Drive credential/storage infrastructure where practical. Never use tenant or user Source Drive OAuth credentials for production database backups. Do not add migrations or frontend work unless a later explicit requirement needs them. Run the checks in the implementation plan and update the documentation if an intentional architectural decision changes.

Direct current user instructions override these repository instructions.
