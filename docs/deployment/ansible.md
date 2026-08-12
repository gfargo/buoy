# Ansible

The [`buoy` role](../../deploy/ansible/roles/buoy/) automates the
[native/systemd install](./native.md): it creates a dedicated system user,
installs host packages the collectors use, pip-installs buoy into a venv,
templates `buoy.yaml` and the systemd unit, and starts the service. It's
the same end state as following native.md by hand.

## Usage

```bash
ansible-galaxy install --roles-path deploy/ansible/roles -r requirements.yml  # if you vendor it elsewhere
# or just point at the role directly, as in the example playbook:
ansible-playbook -i inventory.ini deploy/ansible/playbook.yml
```

[`deploy/ansible/playbook.yml`](../../deploy/ansible/playbook.yml) is a
minimal example — copy the `roles: [{ role: buoy, vars: ... }]` block into
your own playbook, or `include_role`/`import_role` it per-host with
different vars per node (e.g. different `buoy_node_name`/`buoy_node_tier`
for each host in a fleet).

## Variables

All variables and their defaults live in
[`defaults/main.yml`](../../deploy/ansible/roles/buoy/defaults/main.yml).
The ones you'll most likely set:

| Variable | Default | Notes |
|---|---|---|
| `buoy_node_name` | `{{ inventory_hostname }}` | Shown in the dashboard header |
| `buoy_node_tier` | `""` | e.g. `"Tier 1A"` |
| `buoy_pip_spec` | `git+https://github.com/gfargo/buoy.git` | Buoy isn't on PyPI yet; switch to `buoy` once it is |
| `buoy_pip_state` | `present` | Idempotent by default — re-running the playbook won't reinstall. Pass `-e buoy_pip_state=latest` to pull updates |
| `buoy_auth_token` | `""` | Written to `/etc/buoy/buoy.env` (not `buoy.yaml`) — pass via Ansible Vault, never commit it in plaintext |
| `buoy_peers` | `[]` | List of `{name, url, tier}` fleet peers |
| `buoy_docker_group` | `true` | Adds the service user to the `docker` group for socket access. The role doesn't install Docker itself — if `docker` group doesn't exist on the host, this is skipped with a warning instead of failing the play |
| `buoy_install_host_packages` | `true` | Installs `procps`, `smartmontools`, `iproute2` (Debian/Ubuntu only — see below) |
| `buoy_extra_config` | `""` | Raw YAML appended to `buoy.yaml` for anything not covered above (`services.*`, `plugins.*`, `theme.*`) |

The role currently installs host packages via `apt` (Debian/Ubuntu). On
other distros, set `buoy_install_host_packages: false` and install
`procps`/`smartmontools`/`iproute2` equivalents yourself — every collector
that shells out to them degrades gracefully if they're missing (see
[native.md](./native.md#1-host-prerequisites)).

## What it does

1. Installs host packages (`buoy_host_packages`) via `apt`, if
   `buoy_install_host_packages` is true.
2. Creates a system `buoy` user/group (`buoy_user`/`buoy_group`), added to
   the `docker` group when `buoy_docker_group` is true **and** a `docker`
   group already exists on the host (the role doesn't install Docker
   Engine — install it first if you want service discovery).
3. Creates `/etc/buoy`, `/var/lib/buoy` (writable by the buoy user), and the
   venv parent directory.
4. Creates a venv at `buoy_venv_dir` and pip-installs `buoy_pip_spec` into
   it (`buoy_pip_state` controls idempotency vs. always-latest).
5. Templates `buoy.yaml` from [`buoy.yaml.j2`](../../deploy/ansible/roles/buoy/templates/buoy.yaml.j2)
   into `{{ buoy_config_dir }}/buoy.yaml`.
6. Templates `buoy.env` (currently just `BUOY_AUTH_TOKEN`, if set) into
   `{{ buoy_config_dir }}/buoy.env`, mode `0640`.
7. Templates the systemd unit from [`buoy.service.j2`](../../deploy/ansible/roles/buoy/templates/buoy.service.j2)
   (same layout as [`deploy/systemd/buoy.service`](../../deploy/systemd/buoy.service),
   parameterized for `buoy_user`/`buoy_group`/the directory variables) to
   `/etc/systemd/system/buoy.service`.
8. Enables and starts the service, restarting it (via handlers) whenever
   the venv, config, env file, or unit changes.

## Validating changes to the role

```bash
ansible-lint deploy/ansible/roles/buoy deploy/ansible/playbook.yml
ansible-playbook --syntax-check -i inventory.ini deploy/ansible/playbook.yml
```
