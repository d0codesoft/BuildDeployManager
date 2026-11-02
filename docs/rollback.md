# Rollback and crash recovery

```bash
sudo -u scdeploy scdeploy rollback PROJECT --approve
sudo -u scdeploy scdeploy rollback PROJECT --version 1.4.1 --approve
sudo -u scdeploy scdeploy recover PROJECT --approve
```

Rollback itself is journaled and re-verifies retained payload and configured shared links. It stops the current processes before changing paths, switches atomically, starts and verifies the selected previous version. If a rollback attempt fails, compensation restores the state from before that attempt; failure of compensation is explicitly recorded.

Automatic failure recovery restores the old link and exact prior per-service active/inactive state. On a first-install failure with no old link, new services are stopped and the link is removed; a nonexistent old application is never reported as healthy. After a crash/reboot run recover before new work. It replays running/rollback_failed deployment journals under the same project lock. A changed configuration fingerprint, unavailable prior release or failed service recovery requires intervention and is not marked successful.

Manual last-resort procedure, after preserving state/history and verifying actual paths:

1. Stop only the project's affected services.
2. Inspect current, retained releases and the journal's saved previous path.
3. Verify the previous manifest/signature and expected shared data.
4. Create a new temporary symlink beside current, then `mv -Tf` it to current.
5. Start previously active services and check systemctl plus application readiness.
6. Reconcile through `scdeploy recover`; do not edit installed version based on a Git checkout.

The release retention policy protects current and previous, not every historical version. Database schema/data changes need an explicit backward-compatible migration or a separately tested database restore. No generic automatic database downgrade is attempted.
