# Architecture and decisions

The repository root is the Python application root; small related providers are grouped into modules rather than one class per file. This reduces navigation overhead without merging CLI and domain logic.

| Module | Responsibility |
| --- | --- |
| config.py | Versioned dataclass schema, strict recursive type decoding, YAML key and path validation, independent project registry |
| execution.py, worker.py | Checked command execution, deadlines, controlled environment, non-root build worker |
| locks.py | Project deployment locks, build locks and cross-process build slots using kernel locks |
| state.py | SQLite FULL/WAL transactions, installed metadata and permanent operation history |
| sources.py | Git, GitHub Actions/Releases and explicit HTTPS-origin trust |
| builders.py | .NET, Node, Python and custom argv command providers |
| artifacts.py | Canonical manifests, Ed25519 signatures, SHA-256 inventory, bounded TAR and immutable publication |
| deployment.py | Service preflight, staged releases, atomic current switch, readiness, rollback, recovery, retention |
| security.py | Secret profiles, path boundaries, administrative configuration ownership, redaction |
| notifications.py | Safe bounded reports and independent email/Telegram adapters |
| orchestrator.py | Use cases and scheduling policy; contains no argument parsing |
| cli.py | Argument parsing, user output and stable exit codes |
| migration.py | Legacy observation and reviewed cutover plan |

Protocols provide `ISourceProvider`, `IBuildProvider`, `IArtifactProvider`, `IDeploymentProvider`, `IHealthChecker` and `INotificationProvider`. New built-in provider kinds extend the schema and provider selector; adding a project using an existing kind needs YAML only.

SQLite commits the recovery snapshot before services stop. Filesystem and SQLite cannot share one atomic transaction: an interrupted operation is conservatively rolled back using the saved paths, active states and old metadata. Successful installations update SQLite before marking their journal entry complete. A crash between these commits therefore has a recoverable journal entry. Pending recovery blocks new installations until recovery succeeds.

Administrator YAML is a trust boundary because hooks and commands execute code. Build code runs under a separate Unix identity. These identities are not a hostile multi-tenant sandbox: all projects' builds share one build identity, and all deployments share a manager identity. Use separate installations/VMs for mutually untrusted projects.

The primary orchestrator use case is Git HEAD check → clean pinned checkout → local build/test/publish → release transaction. Successful local builds are attested in manager-owned SQLite with a full manifest and archive SHA-256. This ledger authenticates local release installation and rollback without requiring a private signing key on the Ubuntu deployment server. Optional external artifact sources use signed manifests instead. A per-project update lock spans source/build/deploy, and the installation transaction checks the baseline installed commit to reject stale builds after a concurrent manual rollback.
