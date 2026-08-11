# ise-templater

Copy Cisco ISE configuration from one appliance to another.

Read the configuration out of a working appliance, turn it into templates you
can keep in git, and push those templates at a second appliance — a new
deployment, a rebuilt lab, another site.

```
./ise export       ISE  ->  exports/<node>/<resource>.json
./ise templatize   exports/  ->  templates/exported/<resource>/<name>.json.j2
./ise apply        templates/exported/  ->  ISE
./ise destroy      undoes an apply, in reverse order
```

It is Ansible underneath, but **you do not need to know Ansible to use it**.
Every command is `./ise something`. If you want to know what is going on under
there, [docs/ansible-for-newcomers.md](docs/ansible-for-newcomers.md) explains
it in one page.

## What you need

- A Linux or macOS machine that can reach the appliances over HTTPS
- Python 3.12 or newer
- An ISE admin account with the **ERS Admin** and **Super Admin** roles
- Both APIs enabled on the appliance, under
  *Administration → System → Settings → API Settings*

The account is the **GUI** admin, which on ISE is a different login from the
CLI/SSH one. Both APIs have to be on: this project uses both, because neither
one alone exposes all of the configuration.

## First run

```sh
./ise setup                    # installs deps, writes a credentials file
$EDITOR inventory/group_vars/all/99-credentials.yml    # the ISE password
$EDITOR inventory/hosts.yml                            # your appliances
./ise check                  # does every appliance answer?
```

`./ise check` is two API calls per appliance and takes seconds. Do it first —
a wrong password caught here saves finding out forty minutes into an export.

Then the actual job:

```sh
./ise export                 # read the source appliance (slow: tens of minutes)
./ise templatize             # turn the export into templates
./ise apply --dry            # show what would be written, write nothing
./ise apply                  # write it
```

## The commands

| | |
|---|---|
| `./ise setup` | install dependencies, write the credentials file |
| `./ise check` | confirm each appliance answers on both APIs |
| `./ise export` | read configuration into `exports/` |
| `./ise templatize` | turn `exports/` into `templates/exported/` |
| `./ise apply [--dry]` | push `templates/exported/` at the target |
| `./ise sites [--dry]` | push the per-site fan-out from `vars/sites.yml` |
| `./ise destroy [--force]` | remove what the site templates created |
| `./ise lint` | run `ansible-lint` over the playbooks |
| `./ise help` | all of the above, with the options |

Two things to know about the flags:

- **`apply` writes unless you say `--dry`. `destroy` only shows unless you say
  `--force`.** Deletes have to be asked for; that asymmetry is deliberate.
- Anything `./ise` does not recognise goes straight to `ansible-playbook`, so
  `./ise export -e resources=sgt` narrows a run to one resource.

Narrowing a run is the usual way to work once you are past the first export:

```sh
./ise export -e resources=policy-set,condition   # just these two
./ise apply --dry -e resources=policy-set        # check one before writing
./ise export --with portals                      # include an off-by-default family
./ise check --limit ise_target                   # one appliance, not all
```

## Which appliance gets written to

`inventory/hosts.yml` has two groups:

| group | what runs against it |
|---|---|
| `ise_source` | `export`, `templatize` — **read only** |
| `ise_target` | `apply`, `destroy` — **written to** |

Nothing else in this project writes to ISE, so `ise_target` is the entire blast
radius. Check it before an apply.

## Where the password goes

`inventory/group_vars/all/99-credentials.yml` — variables belong in
`group_vars/`, and this is one. It is gitignored; the `.example` beside it is
the committed template and explains every option. Ansible reads it as
variables for every host with no wiring up, and reads nothing at all if it is
not there, which is why the environment variable below still works.

The `99-` is what makes it win: files in that directory are read in name
order, so it lands after `01-connection.yml` and replaces the password worked
out there.

Four places are checked, first hit wins:

| | |
|---|---|
| `-e ise_password=...` | one run, from the command line |
| `99-credentials.yml` | the normal answer |
| `ISE_PASSWORD` | the environment |
| sops | only with `-e ise_password_sops=true` |

It may be encrypted and is read either way:

```sh
ansible-vault encrypt inventory/group_vars/all/99-credentials.yml
./ise check                  # prompts, or reads .vault-pass if you made one
```

Different accounts per appliance go in `inventory/host_vars/<node>.yml`.

Nothing carrying a secret is committable: the credentials file, `.vault-pass`
and `exports/` are all gitignored. Raw exports hold RADIUS shared secrets and
repository credentials in cleartext — templates are the artefact meant for git.

## Where everything lives

Standard Ansible layout: playbooks in `playbooks/`, the inventory and its
variables in `inventory/`, the work itself in `roles/`. The only thing in the
project root you run is `./ise`.

```
ise                       the command you type

playbooks/                one per command, named after it. Each is a dozen
  check.yml               lines -- which appliances, how to authenticate,
  export.yml              and which roles to run
  templatize.yml
  apply.yml
  destroy.yml

inventory/                which appliances, and the settings that go with
  hosts.yml               them. group_vars/ and host_vars/ are read from
                          beside the inventory, so they live here too --
                          split them up and Ansible silently loads nothing
  group_vars/all/           settings every host shares, read in name order
    00-paths.yml              where the exports and the templates live
    01-connection.yml         addresses, ports, timeouts, credentials
    02-families.yml           which families of configuration to touch
    03-catalog.yml            every resource this project knows about
    04-templating.yml         what gets stripped and rewritten
    99-credentials.yml        your password (gitignored). Last, so it wins
  host_vars/                settings for one appliance only

vars/                     data the playbooks are pointed at
  sites.yml                 the sites the fan-out builds

roles/                    the work. One per command, plus a shared one
  ise_common/               the password check, and which resources a run
                            touches
  ise_check/
  ise_export/
  ise_templatize/
  ise_apply/
  ise_destroy/

filter_plugins/ise.py     the handful of things Jinja cannot say cleanly

templates/
  exported/               one file per exported object, written by
                          ./ise templatize -- edits here are lost on the
                          next run
  site/                   one file per kind of object, hand-written,
                          rendered once per site

exports/                  raw exports (gitignored)
docs/                     the long explanations
```

A role is a directory of task files with a `tasks/main.yml` that Ansible runs
first. To follow what `./ise apply` does, open `playbooks/apply.yml`, see that
it runs `ise_common` then `ise_apply`, and read
`roles/ise_apply/tasks/main.yml`.

## Reading more

| | |
|---|---|
| [docs/ansible-for-newcomers.md](docs/ansible-for-newcomers.md) | what Ansible is doing here, and how to read a run |
| [docs/workflow.md](docs/workflow.md) | each step in detail, including policy sets and per-site fan-out |
| [docs/catalog.md](docs/catalog.md) | the 79 resources, the families, and what each catalog key means |
| [docs/ise-api-notes.md](docs/ise-api-notes.md) | how ISE's two APIs actually behave, and what they will not accept back |
| [docs/troubleshooting.md](docs/troubleshooting.md) | error messages, and what they mean |

## Installing without `uv`

`./ise setup` uses [uv](https://docs.astral.sh/uv/) and the pinned `uv.lock`.
It is one command to install and worth having:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Without it, a plain virtualenv works and `./ise` will find it:

```sh
sudo apt install -y python3-venv          # Ubuntu 24.04 ships Python 3.12.3
python3 -m venv .venv && .venv/bin/pip install 'ansible-core>=2.21,<2.22'
```

Either way it is wheels the whole way down — no compiler, no `-dev` packages.
The `uv` route also installs the authoring tools (`ansible-lint` and friends)
that `./ise lint` uses; the plain-pip route does not.

`uv` verifies TLS against the operating system's certificate store, so a
network that inspects TLS works without further ceremony: `system-certs = true`
in `pyproject.toml`, and `./ise` exports `UV_SYSTEM_CERTS=1` to cover `uv` runs
from elsewhere. On `uv` older than 0.10 both are spelled `native-tls` /
`UV_NATIVE_TLS`.

## How it is built

Every task is `ansible.builtin.uri` — an HTTPS call. No Ansible collection, no
`ciscoisesdk`, no SSH. `ansible_connection` is `local` for every host, and an
appliance's address is used for nothing but building a URL. The one departure
from stock Ansible is `filter_plugins/ise.py`, a few hundred lines doing the
things Jinja cannot express cleanly.

Object **name** is the identity throughout. Ids are never carried between
appliances, because each appliance mints its own.
