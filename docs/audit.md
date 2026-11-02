# Audit of the supplied implementation

Legacy deployment scripts were audited and removed from the public repository and its history because they contained personal and infrastructure details. The table below summarizes the replaced behavior.

| Input | Existing behavior | Findings and replacement |
| --- | --- | --- |
| build.py | clone, pull, restore/build, two publishes, TAR | Common fixed workspace, hard-coded projects/runtime, token in URL, no test phase or signed manifest. Fresh owned workspaces, isolated outputs, credentials through askpass, explicit tests and authenticated artifact store replace it. |
| upgrade_site.py | Git checks, Actions/Releases helpers, systemctl, link switching, health, mail | Unchecked build commands; unsafe extractall; destructive Git clean/reset; unlink then symlink; service rollback without stopping new processes; ignored rollback failures; plaintext installed SHA; latest run not validated. Replaced with checked providers and a write-ahead deployment journal. |
| email_sender.py | HTML log email and severity colors | Literal password, unverified TLS and unlimited logs. Literal values were removed; new module verifies TLS, escapes HTML, limits size and isolates channel failures. |
| init_server.sh | All-in-one machine provisioning | Blanket upgrades, overwriting Nginx/mail, firewall activation, database credentials, duplicate backend unit, swap/sysctl changes. Manager install, explicit tool install and explicit per-project provisioning now have separate scripts. |
| scloud.conf | TLS reverse proxy, /wiki, custom headers | Preserve routes, certificate paths and all virtual hosts. Has conflicting X-Frame-Options headers; administrator must review, not automatically rewrite. Optional reviewed apply procedure validates nginx and retains a backup. |
| kestrel-scloud.service | /var/www/scloud, scloud user, notify/watchdog | Differs from init_server's /opt/scloud and scode identity. Environment filenames also differ. Live unit properties must be inspected; process notify support must be checked. |
| next-scloud-client.service | Next.js on port 3000 | Root process and API base port 5000 differ from backend 5770. Requires a separate non-root migration and a separate project configuration. Not silently appended to backend deployments. |
| test_email_sender.py | Sends real mail at import | Not an automated test and unsafe for discovery. New pytest suite collects only tests/, uses fake mail/Telegram transports and never contacts production. |
| update_geoip.sh | Replaces downloaded MMDB files | Separate mutable operational data; keep under shared/. Multi-file update is not globally atomic. Not part of immutable app releases. |
| requirements.txt | requests dependency | New transport uses the standard library; schema/signature/version dependencies are explicitly declared. |
| README.md | Empty | Replaced by application README and operational documentation. |

Exposed credential literals in reference files were sanitized without copying their values. Existing GitHub tokens, SMTP passwords and database passwords must be revoked/replaced by the administrator. Removing literals does not revoke credentials or clean external historical copies. New profiles must contain newly issued secrets only.

The actual remote SCLOUD server is not connected. Live paths, authoritative units, backup restoration and package/tool availability cannot be established from example files. The read-only import command and migration checklist collect this evidence before cutover.
