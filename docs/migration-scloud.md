# SCLOUD: observation, rehearsal and explicit cutover

The supplied configuration defaults to observation. No remote production server was accessed or modified during development. First collect authoritative evidence:

```bash
sudo -u scdeploy scdeploy validate scloud
sudo -u scdeploy scdeploy import-legacy scloud --json
systemctl show kestrel-scloud.service scloud.service --property=LoadState,ActiveState,WorkingDirectory,ExecStart,User,FragmentPath
readlink -f /var/www/scloud
sudo nginx -t
```

Observation records unit properties, path types, active Nginx file paths and SHA-256 fingerprints in operation history. It does not export secrets from environment files or claim to have made complete application/database backups. The import plan is preserved as safe audit metadata. Actual backups are explicit administrator operations.

The reference kestrel unit runs ExampleServer.dll from `/var/www/scloud`; init_server.sh instead defines scloud.service under `/opt/scloud`. Identify the real authoritative unit and ensure the duplicate does not start another process. Check runtime user, environment file (`scode-env` versus `scloud-env`), port 5770, Type=notify/watchdog support and the /health endpoint. The Node client is a separate root-running service using a different API base port; migrate it independently to a non-root identity/project.

Before cutover, back up the authoritative unit including drop-ins, active Nginx files and certificates, current application files/configuration/uploads/GeoIP and a consistent PostgreSQL backup. Verify recovery on an isolated Ubuntu VM. Preserve `/wiki` routing and other hosts. Do not run the legacy init script. New source configuration uses the actual ExampleServer and ExampleAdmin csproj paths, publishing server and admin separately; verify repository structure before building.

Create an initial retained release from the existing running files and generate/sign a manifest on a trusted host. Restore its mutable paths under shared with the same runtime permissions. During a separately authorized maintenance window, stop the authoritative backend, establish `/opt/scloud/releases/LEGACY_VERSION` and `/opt/scloud/current`, and change its unit to WorkingDirectory=/opt/scloud/current/server and ExecStart=/usr/bin/dotnet /opt/scloud/current/server/ExampleServer.dll (or the self-contained executable). Preserve all other required properties. Adjust the admin path to current/admin as appropriate.

If `/var/www/scloud` is a real directory, retain it intact or rename it to an explicit backup **only after administrator review**. Never overwrite or unlink it automatically. If Nginx needs that path for static assets, review whether a compatibility link to current/server is appropriate; keep backend and wiki routes/TLS intact. Provision only exact start/stop/restart sudo rules for the authoritative service; never grant broad root commands. Service daemon-reload is an administrator action.

Start the legacy payload through the new current path and verify process, health, static paths, admin and wiki. Rehearse rollback to this retained version before enabling updates. After successful rehearsal set observe_only=false in root-owned YAML, install the trusted public key and use an explicit signed-artifact deployment with --approve. Keep updates.mode=manual for the first production cycles. Do not infer installed metadata from the build repository; install the retained signed baseline through the manager as part of the reviewed cutover so rollback history is established.

If cutover fails, stop the backend, restore the saved unit/drop-ins and original application paths, daemon-reload, start the original service and validate health plus Nginx. Restore databases only using the independently tested backup plan. This repository intentionally does not auto-execute a production migration.
