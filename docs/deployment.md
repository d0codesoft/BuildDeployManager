# Deployment and state

The primary operation for Git/GitHub projects is one command:

```bash
sudo -u scdeploy scdeploy deploy PROJECT --approve
```

It checks HEAD of the configured branch (master for SCLOUD), compares it with installed_commit, and skips clone/build/service changes if the commit is already installed. If it differs, the manager pins the full SHA, creates a clean clone in a new workspace, runs restore/build/test/publish and creates an immutable local artifact. Only then does it enter the release transaction below. A failed build leaves the running service and installed commit unchanged. A moved branch during the build does not change the pinned checkout; the next check discovers the newer HEAD. Failed deployments remain eligible for a retry because installed_commit changes only after successful health verification.

`--force` rebuilds even the same installed commit; each default commit-based build gets a unique release identifier `<SHA-prefix>-<build-id-prefix>`. `--version` supplies an explicit immutable release version and `--commit` optionally pins a full commit. An update lock covers check/build/deploy, while the deployment lock guards service transitions. A concurrent rollback/manual installation during a build causes the stale pipeline to abort before stopping any service. The manager uses fresh checkouts rather than resetting a user-owned worktree.

Local build trust uses the manager-owned successful build journal, the full manifest and archive SHA-256. Private signing keys are unnecessary for this local pipeline. The build identity cannot write the journal or artifact store. Retained local releases are verified against the same build records during rollback/recovery. Passing an unsigned external file with --artifact cannot claim this local trust path; external artifacts require a signature by default. Do not delete successful build history needed to authenticate retained local releases.

For local Git releases identified by commit/build ID, upstream branch HEAD determines the selected version; numeric semantic-version ordering is not applied to opaque SHAs. Explicit semantic versions and external artifacts still use downgrade protection. A forced branch rewrite on GitHub is therefore treated as the new selected HEAD. Keep production Git write access protected.

Installing an externally prepared archive is an additional workflow:

```bash
sudo -u scdeploy scdeploy deploy PROJECT --artifact /safe/app.tar.gz --dry-run --json
sudo -u scdeploy scdeploy deploy PROJECT --artifact /safe/app.tar.gz --approve
sudo -u scdeploy scdeploy status PROJECT --json
sudo -u scdeploy scdeploy history PROJECT --json
```

The selected archive is copied into private staging and verified there, so source changes cannot race archive verification. Under a project kernel lock the manager recovers interrupted operations, checks service paths, extracts into staging, validates a signed manifest/inventory/runtime and version policy, runs reviewed pre-deploy hooks, validates again and attaches explicit shared links. A completed release is renamed into the retained release directory. Existing versions cannot be overwritten with conflicting contents. Repeating the active same release is a no-op.

Before stopping services, the SQLite journal saves the previous link, exact project metadata, active states, deployment configuration fingerprint, actor and artifact hash. Every stop and start return code is checked. The manager creates a temporary symlink and swaps it with os.replace, then starts all configured services and checks process states plus readiness. Only successful readiness updates installed metadata. Failed attempts remain in permanent history. A failed stop, start, healthcheck or cancellation initiates compensation; failed compensation is an explicit rollback_failed state and exit code 6.

Installed metadata includes version/commit, available version/commit, build/artifact/deployment identifiers, source repository, installation time/result, prior version/path, approval, readiness and failures. Completed releases are retained according to policy while always protecting current and previous. Cleanup never removes shared data or operation history. Keep a separate state backup and independently backed-up database/application data.

The `shared` mapping adds symlinks for pre-existing mutable paths, such as configuration, uploads or GeoIP databases. These destinations must be provisioned before deployment and cannot overwrite artifact files. Neither application data nor database snapshots are implicitly rolled back. Use backward-compatible schema changes, maintenance windows and explicit tested backup/restore hooks.

There is a stop/start availability gap. Atomic filesystem switching does not provide zero downtime. Blue/green proxy switching is outside this deployment provider and must be added as a separate provider if required. The manager never restarts Nginx, PostgreSQL, firewall or mail during an ordinary update.
