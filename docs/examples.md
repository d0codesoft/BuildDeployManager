# Adding projects and notifications

Five independent schema-valid examples are supplied: scloud, spc-webserver, spc-pushgateway, example-node and example-python. Only SCLOUD repository/csproj values come from supplied source; other repository/API/path examples must be adjusted to actual deployments.

```bash
sudo install -o root -g scdeploy -m 0640 projects/examples/example-python.yaml /etc/scdeploy/projects/example-python.yaml
sudo -u scdeploy scdeploy validate example-python
sudo -u scdeploy scdeploy show example-python --json
sudo -u scdeploy scdeploy build example-python --version 1.0.0
sudo -u scdeploy scdeploy deploy example-python --approve
sudo -u scdeploy scdeploy deploy example-python --approve --force
```

For a custom build, replace the build section with an argv plan; the command must create a publication directory:

```yaml
build:
  provider: command
  commands:
    - [python3, tools/build.py, --output, dist]
  test_commands:
    - [python3, -m, unittest, discover]
  publish_directory: dist
  tests: true
```

Newly issued SMTP profile shape (placeholders only):

```json
{"host":"mail.example.com","port":587,"tls":"starttls","username":"sender@example.com","password":"NEW_SECRET","from":"sender@example.com","to":["admin@example.com"]}
```

Telegram profile shape:

```json
{"token":"NEW_BOT_TOKEN","chat_id":"CHAT_ID"}
```

Install profiles as scdeploy-owned mode 0600 files. Reference their names from notifications.email.profile or notifications.telegram.profile and enable the channel. Do not put credentials into source-controlled YAML or command-line options. Notifications are limited to bounded reports and per-channel errors are independent of the build/deployment result.

Ready artifact project: configure source.type=http, metadata_url, explicit trusted_hosts and public_key; use deploy --approve without --artifact to fetch the selected signed release. For GitHub Actions use github-actions, owner/name repository, workflow filename, exact artifact_name and the trusted manifest key. For a tagged release use github-release with explicit tag and exact TAR asset name.
