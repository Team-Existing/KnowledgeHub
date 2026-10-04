# Inactive accounts and groups

**Anything unused for a year is deleted, with its data.** The period is `RETENTION_INACTIVE_DAYS` (default 365).

| What | Counts as active | Deleted when idle |
|---|---|---|
| Account | signing in, or any use of the app | the account, its personal space and everything in it, its group memberships and invitations, and the GraphRAG questions it asked in groups |
| Group | any member working in the group's space | the group, its memberships, and everything in its space |

- **What a deleted user added to a group stays in the group.** If they were a group's admin, the longest-standing remaining member becomes admin. A group with no other members goes with them.
- **Safety rails:**
  - The last server admin is never deleted.
  - Spaces in the middle of a connector sync are skipped until the next run.
  - On upgrade, existing accounts and groups start their clock then, so nothing is deleted for inactivity from before tracking existed. This happens once.
- **When it runs.** Every `RETENTION_CHECK_HOURS` (default 24). In production, Celery beat schedules it on the worker; in development the API runs it at startup and then on that interval. Runs are locked, so the scheduled run and an admin's **Delete these now** never overlap. Set `RETENTION_ENABLED=false` to switch it off; admins still see what it *would* delete.
- **Visibility.** On the **Users** page, server admins see each account's last activity and expiry date, what's due now, what's due within 30 days, and the last run, and can delete what's due immediately. The **Groups** page shows each group's expiry date.

Activity is written at most once an hour per account or group, outside the request's transaction, so it doesn't slow down normal use.
