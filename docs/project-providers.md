# Sources, builds and authenticated artifacts

Git lookup uses ls-remote without touching a worktree. Fetch always clones into a new owned directory, fetches the exact full SHA and checks out detached HEAD. Existing caller directories are rejected. `fetch` retains its workspace for inspection; full `build`/`test` creates and cleans disposable workspaces. There is no reset/clean of a user checkout. Askpass receives tokens outside argv and remote URLs; tokens are registered for redaction before calling Git. SSH credentials/known_hosts remain the administrator's responsibility.

`deploy` uses this source path by default for git/github: check installed versus remote SHA, fresh pinned clone, local build/test/publish, then verified release switching. No GitHub Actions artifact or pre-existing archive is required. A successful build records the full manifest and archive digest in the protected manager journal. This record authenticates installation and future rollback of local builds without a private signing key. Downloaded artifacts use the separate signature verification path.

GitHub Actions checks the exact workflow ID, successful completed status, selected branch and commit, originating head repository, trusted event, non-expired artifact and its workflow commit. It chooses a successful exact match, never an arbitrary latest run. The download ZIP must contain exactly one signed application TAR.GZ. Use explicit trusted_hosts if GitHub's storage host differs from the default allowlist. Credentials are stripped on redirects to a different host. HTTPS is mandatory for artifacts.

GitHub Releases requires a stable published explicit tag and uniquely named asset, resolves the tag commit independently, and checks that commit against the signed manifest. HTTP metadata contract:

```json
{"version":"1.4.2","commit":"FULL_SHA","artifact_url":"https://updates.example.com/files/app.tar.gz"}
```

Metadata is discovery information, not proof of authenticity. Only a valid manifest signature under a configured trusted key authorizes provenance; a checksum from the same endpoint is insufficient. Signed manifest project, version (for tagged/HTTP releases), commit, runtime and file inventory must match the selected update. Actions obtains the semantic version from the signed manifest and binds provenance to the selected workflow commit.

.NET restores/builds each project with the runtime and SelfContained property, tests at the configured working-directory solution level, then publishes each project to its own output. SDK selection follows installed dotnet/global.json. Node uses npm ci when package-lock.json exists, otherwise npm install; checks node_major, tests, runs build and publishes the configured directory. Python creates a disposable venv, installs requirements and test/build tools as needed, tests, optionally builds wheel/sdist and copies the publication directory. Runtime Python environment installation is an administrator-managed service/bootstrap step; do not copy a build venv to another release path. Custom commands execute as argv lists through the same deadline/worker mechanism.

The artifact root contains manifest.json plus payload files. Manifest schema 1 includes project/version/commit/runtime/build_configuration/created_at/files and optional signature. Automatic remote-artifact deployment additionally requires signed source_branch matching the configured allowlisted branch; automatic local Git builds bind branch provenance in the protected build record. Normal branch builds include this field; arbitrary explicit-commit/tag builds omit it rather than asserting unverified branch membership. External CI signers must bind branch provenance in the signed manifest. Every file has path, size, SHA-256 and executable bit. Files are sorted and JSON is canonicalized with sorted keys, compact separators, ASCII encoding, excluding signature. The Ed25519 signature is base64. Artifacts reject all symlinks, hardlinks, devices and traversal entries; shared links are added only after verification. TAR limits: 4 GiB expanded, 100000 entries. Metadata: 8 MiB. Local build attestation or remote signature and file inventory are checked before stopping a service.

Generate a key on a trusted signing host:

```bash
openssl genpkey -algorithm ED25519 -out signing.pem
chmod 600 signing.pem
openssl pkey -in signing.pem -pubout -out application-public.pem
```

Install only the public key as root under `/etc/scdeploy/trust`. Build without private keys on production by default. On a trusted signing/build host, `scdeploy build PROJECT --version 1.4.2 --signing-key /secure/signing.pem` signs after worker processes finish. Treat the manager/signing host as trusted; source code must not have manager identity access. No command transmits signing keys. An unsigned artifact may be used only if an administrator explicitly disables require_signature for a manual isolated test; automatic deployment rejects that policy.
