# Schema 1 field reference

Generated from src/scdeploy/config.py. See configuration.md for semantics.

## Config

| Field | Type | Default |
| --- | --- | --- |
| project | `<class 'Project'>` | `required` |
| source | `<class 'Source'>` | `required` |
| deploy | `<class 'Deploy'>` | `required` |
| schema_version | `Literal[1]` | `1` |
| build | `<class 'Build'>` | `Build()` |
| updates | `<class 'Updates'>` | `Updates()` |
| notifications | `<class 'Notifications'>` | `Notifications()` |

## Project

| Field | Type | Default |
| --- | --- | --- |
| name | `<class 'str'>` | `required` |
| description | `<class 'str'>` | `''` |
| enabled | `<class 'bool'>` | `True` |
| environment | `<class 'str'>` | `'production'` |

## Source

| Field | Type | Default |
| --- | --- | --- |
| type | `Literal['git', 'github', 'github-actions', 'github-release', 'http']` | `required` |
| repository | `<class 'str'>` | `''` |
| branch | `<class 'str'>` | `'main'` |
| tag | `str \| None` | `None` |
| auth_profile | `str \| None` | `None` |
| workflow | `str \| None` | `None` |
| artifact_name | `<class 'str'>` | `'application'` |
| metadata_url | `str \| None` | `None` |
| trusted_hosts | `list[str]` | `list()` |
| public_key | `str \| None` | `None` |

## BuildProject

| Field | Type | Default |
| --- | --- | --- |
| path | `<class 'str'>` | `required` |
| output | `<class 'str'>` | `required` |

## Build

| Field | Type | Default |
| --- | --- | --- |
| provider | `Literal['dotnet', 'node', 'python', 'command']` | `'dotnet'` |
| configuration | `Literal['Release', 'Debug']` | `'Release'` |
| runtime | `<class 'str'>` | `'linux-x64'` |
| self_contained | `<class 'bool'>` | `True` |
| projects | `list[BuildProject]` | `list()` |
| restore | `<class 'bool'>` | `True` |
| tests | `<class 'bool'>` | `True` |
| timeout_seconds | `<class 'int'>` | `1800` |
| working_directory | `<class 'str'>` | `'.'` |
| publish_directory | `<class 'str'>` | `'dist'` |
| nuget_config | `str \| None` | `None` |
| node_major | `int \| None` | `None` |
| python_executable | `<class 'str'>` | `'python3'` |
| requirements | `str \| None` | `None` |
| distribution | `<class 'bool'>` | `False` |
| commands | `list[list[str]]` | `list()` |
| test_commands | `list[list[str]]` | `list()` |
| environment | `dict[str, str]` | `dict()` |
| cpu_quota | `str \| None` | `None` |
| memory_max | `str \| None` | `None` |

## Deploy

| Field | Type | Default |
| --- | --- | --- |
| release_directory | `<class 'str'>` | `required` |
| current_link | `<class 'str'>` | `required` |
| provider | `Literal['systemd-symlink']` | `'systemd-symlink'` |
| services | `list[str]` | `list()` |
| healthchecks | `list[Healthcheck]` | `list()` |
| rollback | `<class 'Rollback'>` | `Rollback()` |
| shared | `dict[str, str]` | `dict()` |
| pre_deploy | `list[list[str]]` | `list()` |
| observe_only | `<class 'bool'>` | `True` |
| require_signature | `<class 'bool'>` | `True` |

## Healthcheck

| Field | Type | Default |
| --- | --- | --- |
| type | `Literal['http', 'tcp', 'systemd', 'command']` | `'http'` |
| url | `<class 'str'>` | `''` |
| expected_status | `<class 'int'>` | `200` |
| body_contains | `str \| None` | `None` |
| host | `<class 'str'>` | `'127.0.0.1'` |
| port | `<class 'int'>` | `0` |
| service | `<class 'str'>` | `''` |
| command | `list[str]` | `list()` |
| timeout_seconds | `<class 'int'>` | `60` |
| interval_seconds | `<class 'float'>` | `2.0` |
| attempts | `<class 'int'>` | `30` |

## Rollback

| Field | Type | Default |
| --- | --- | --- |
| enabled | `<class 'bool'>` | `True` |
| keep_releases | `<class 'int'>` | `5` |

## Updates

| Field | Type | Default |
| --- | --- | --- |
| mode | `Literal['manual', 'notify', 'automatic']` | `'manual'` |
| check_interval | `<class 'str'>` | `'30m'` |
| allowed_branches | `list[str]` | `list()` |
| require_approval | `<class 'bool'>` | `True` |
| allow_downgrade | `<class 'bool'>` | `False` |
| maintenance_start_utc | `<class 'int'>` | `0` |
| maintenance_end_utc | `<class 'int'>` | `24` |
| max_failures | `<class 'int'>` | `3` |

## Notification

| Field | Type | Default |
| --- | --- | --- |
| enabled | `<class 'bool'>` | `False` |
| profile | `<class 'str'>` | `''` |
| on_success | `<class 'bool'>` | `True` |
| on_failure | `<class 'bool'>` | `True` |

## Notifications

| Field | Type | Default |
| --- | --- | --- |
| email | `<class 'Notification'>` | `Notification()` |
| telegram | `<class 'Notification'>` | `Notification()` |
