# SPC.Update.Server service for Ubuntu

The unit in `systemd/spc-update-server.service` was derived from the local project
`/path/to/example-update-server`: its csproj targets net10.0
with no AssemblyName override; Program.cs maps /health and supports explicit
--migrate-only; StorageDirectoryService accepts absolute storage paths.
The configured development HTTP port is 5151; this unit explicitly selects that
port for its production loopback backend. Production ports are an administrator
choice, not a launchSettings.json runtime setting.

The unit expects the Build & Deploy Manager to publish the server csproj into
the `server` output:

```yaml
build:
  provider: dotnet
  runtime: linux-x64
  self_contained: false
  projects:
    - path: SPC.Update.Server/SPC.Update.Server.csproj
      output: server
deploy:
  release_directory: /opt/spc-update-server/releases
  current_link: /opt/spc-update-server/current
  services: [spc-update-server.service]
  healthchecks:
    - type: http
      url: http://127.0.0.1:5151/health
      expected_status: 200
      body_contains: '"status":"ok"'
```

Install the ASP.NET Core 10 runtime separately when publishing framework-dependent
binaries. For self-contained publication the DLL invocation still needs dotnet;
to remove that requirement explicitly change ExecStart to the published
`/opt/spc-update-server/current/server/SPC.Update.Server` executable.

Run these commands from a copy of this repository on Ubuntu:

```bash
id spc-update-server >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin spc-update-server
sudo install -d -o root -g root -m 0755 /etc/scdeploy/apps
# Copy the example only once; do not overwrite existing production settings.
sudo test -e /etc/scdeploy/apps/spc-update-server.env || sudo install -o root -g root -m 0600 projects/examples/spc-update-server.env.example /etc/scdeploy/apps/spc-update-server.env
sudoedit /etc/scdeploy/apps/spc-update-server.env
sudo install -o root -g root -m 0644 systemd/spc-update-server.service /etc/systemd/system/spc-update-server.service
sudo systemd-analyze verify /etc/systemd/system/spc-update-server.service
sudo systemctl daemon-reload
```

Replace the database placeholders with the actual existing database credentials.
The exact connection string key for the project's PostgreSQL provider is
`ConnectionStrings__Postgre`, not `ConnectionStrings__DefaultConnection`.
The PostgreSQL server and database must already be provisioned. Startup migrations
and EnsureCreated are disabled: review and apply database migrations separately
before running a new version. This service does not automatically create a
database, run migrations, change Nginx or grant manager sudo privileges.

The published DLL must exist before starting the service. For an existing managed
installation, grant `scdeploy` the exact start/stop/restart permissions described
in installation.md; provision-project.py grants these for a new application root.
The runtime identity `spc-update-server` remains separate from manager and builder
identities. Do not recursively chown the managed release tree to the runtime user.

After publishing the first release and establishing current/server:

```bash
sudo systemctl enable --now spc-update-server.service
systemctl status spc-update-server.service --no-pager
curl -i http://127.0.0.1:5151/health
journalctl -u spc-update-server.service --no-pager -n 100
```

`Type=exec` does not claim application readiness; the manager's HTTP healthcheck
does that separately. The current /health implementation returns status=ok and
does not probe database health. HTTP-to-HTTPS redirection middleware may affect
local checks if an HTTPS redirect port is configured elsewhere; review the actual
response instead of accepting a redirect as readiness.

Mutable work and distributed update packages live in `/var/lib/spc-update-server`,
not inside a retained application release. Default ASP.NET Core Data Protection
keys can persist under its HOME. Their permissions, backups and encryption at rest
remain operational responsibilities. Logs go to journald; /var/log is available
for a future explicitly configured file logger. Existing data in old relative
`data/work` or `data/releases` must be migrated separately before switching paths.

The listener is HTTP on loopback for an independently configured HTTPS reverse
proxy. Keep 5151 off the public interface; the proxy must replace forwarded
headers. The application also supports client-certificate authentication; HTTPS
termination at the proxy does not automatically forward client certificates.
For that authentication mode, separately configure and validate certificate
forwarding or direct Kestrel TLS. This unit does not supply certificates.

The service was checked against project source and manager path conventions.
Actual systemd startup and unit verification must be run on Ubuntu; they were not
executed in the Windows development environment.
