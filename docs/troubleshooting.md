# Troubleshooting

| Symptom | Evidence and action |
| --- | --- |
| Exit 2 | YAML/schema/path error. Run validate, check unknown fields, absolute deployment roots and protected ownership. |
| Exit 3 | Policy refusal. Check observation, --approve, service migration, signature requirement and maintenance policy. |
| Exit 4 | Project/build lock or build limit occupied. Wait for the owning process; kernel locks release on process exit. Do not delete lock files to bypass a live process. |
| Exit 5 | Artifact signature, project/runtime/version or file inventory failed. Inspect signing trust and exact source commit; never disable verification in production. |
| Exit 6 | Rollback/recovery failed. Preserve history/journal; restore original configuration and previous release, then run recover. |
| Exit 130 | Cancelled operation. Inspect history; deployment compensation runs on normal cancellation. After a hard kill/reboot run recover. |
| Build tool unavailable | doctor shows tool paths. Install the specific supported tool; dotnet global.json and node_major govern versions. |
| Worker permission failure | Check scdeploy membership in scdeploy-build, workspace ownership/modes and sudoers for the unprivileged worker. Start a fresh login/job after group membership changes. |
| Resource scope unavailable | Provision a user systemd bus/lingering session. Quota requests fail rather than silently run without constraints. |
| Unit preflight rejects legacy | Run import-legacy; review actual ExecStart, WorkingDirectory and runtime User. Do not force an update under old paths. |
| Failed health after start | logs reads bounded status/journal. Check configured port/readiness body, service user, shared files and environment. |
| Repeated automatic failures | Automatic policy suspends at max_failures. Diagnose manually, perform reviewed recovery/deploy; successful deploy resets the counter. |
| Email/Telegram failure | Primary result remains unchanged. Check newly issued profiles, mode 0600, network and valid TLS chain. No unverified TLS option exists. |

Technical logs rotate under `/var/log/scdeploy`. Build and deploy subprocess logs are bounded per command. SQLite audit history is independent of release cleanup. JSON command results go to stdout; diagnostics go to stderr. `logs PROJECT` needs read permission for system journal; provision read access explicitly if desired. It grants no root journal access automatically.
