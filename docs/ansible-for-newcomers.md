# Ansible, as this project uses it

You can run everything here with `./ise` and never read this page. It is for
when something goes sideways, or when you want to change what the playbooks do.

Ansible is normally introduced as a tool for configuring servers over SSH.
That is not what is happening here. **This project never connects to anything.**
Every step is an HTTPS request to an ISE API, made from the machine you are
sitting at. Ansible is being used as a way to write "for each of these 79
resources, fetch it, transform it, write it somewhere" without writing a
program.

## The six words worth knowing

**Inventory** — the list of machines. `inventory/hosts.yml` here. Ours has two
groups, `ise_source` and `ise_target`, and each host is a name plus an address.
The name is a label; the address is what goes in the URL.

**Playbook** — a file of steps, run top to bottom. The five in `playbooks/`
and the rest. Each one is a *play*: a set of hosts plus the work to run
against them. In this project they are short — a play names its appliances,
says how to authenticate, and hands off to roles.

**Role** — a named bundle of tasks in `roles/<name>/`, with `tasks/main.yml`
as the way in. There is one per command (`ise_export`, `ise_apply`, …) plus
`ise_common`, which every play runs first to check the password is there and
work out which resources the run touches. A role's other task files sit beside
`main.yml` and are pulled in by name, so `include_tasks: resource.yml` inside
`roles/ise_apply/` means `roles/ise_apply/tasks/resource.yml`.

**Task** — one step. It names a *module* and gives it arguments. Almost every
task in this project uses the `uri` module, which makes an HTTP request:

```yaml
- name: Fetch the network device groups
  ansible.builtin.uri:
    url: "https://10.200.30.10:9060/ers/config/networkdevicegroup"
    method: GET
```

**Variable** — a named value. `{{ ise_user }}` in a task means "substitute the
variable `ise_user` here". Variables live in `inventory/group_vars/all/`,
which applies to every host, and can be overridden on the command line.

**Template** — a file with `{{ ... }}` in it, filled in when it is used. The
`.j2` files under `templates/exported/` are ISE objects in JSON with the
deployment-specific bits replaced by variables.

## Reading a run

```
TASK [ise_export : Export via ERS: sgt] ****************************************
ok: [labb-ise-001]
```

Each task prints its name and then one line per host.

| | |
|---|---|
| `ok` | the task ran and changed nothing |
| `changed` | the task ran and changed something |
| `skipping` | a `when:` condition said this task did not apply |
| `failed` | it did not work, and the run usually stops here |

One trap worth knowing early: **`uri` reports a successful write as `ok`, not
`changed`.** Ansible cannot tell whether a POST changed anything, so the
`changed` count is not a count of what you wrote. `./ise apply --dry` is the
readable account of what a run will do.

The `PLAY RECAP` at the end is the same numbers totalled up. `failed=0` is what
you are looking for.

Skipped tasks are hidden by `display_skipped_hosts = False` in `ansible.cfg`.
A full run skips hundreds of them — every resource not in this run, every
branch not taken — and showing them buries everything else.

## Flags worth knowing

Anything `./ise` does not recognise is passed to `ansible-playbook`, so these
all work as `./ise export <flag>`:

| flag | what it does |
|---|---|
| `-e name=value` | set a variable for this run. Beats every other source |
| `-e @file.yml` | set a whole file's worth of variables |
| `--limit ise_target` | run against some hosts, not all of them |
| `-v`, `-vv`, `-vvv` | more detail. `-vvv` shows the full HTTP requests |
| `--start-at-task "name"` | skip ahead to a named task |
| `--list-tasks` | print what would run, without running it |

`-e` is the one you will use. `-e resources=sgt` narrows a run to one resource;
`-e ise_dry_run=true` is what `--dry` expands to.

`--check` (Ansible's own dry-run mode) is **not** what you want here — use
`--dry`. Check mode skips tasks rather than reporting on them, and a run that
cannot read the appliance cannot tell you what it would write.

## Where the variables come from

Highest wins:

1. `-e` on the command line
2. `vars:` in the playbook — the switches one command owns, like
   `ise_dry_run` in `playbooks/apply.yml`
3. `inventory/host_vars/<name>.yml` — settings for one appliance
4. `inventory/group_vars/all/*.yml` — settings for all of them, read in name
   order, so `99-credentials.yml` beats the rest of the directory
5. defaults in `roles/*/defaults/main.yml`

So a password in `99-credentials.yml` overrides the one `01-connection.yml`
works out from the environment, and `-e` overrides everything.
That is the whole precedence story for this project; Ansible's full version has
more layers, and none of them are used here.

## Changing things

**Add an appliance**: copy a two-line host block in `inventory/hosts.yml`. Run
`./ise check`.

**Change what is collected**: `inventory/group_vars/all/02-families.yml`
switches whole families on and off. For one run, `./ise export --with
portals`.

**Add a resource**: add an entry to `ise_catalog` in
`inventory/group_vars/all/03-catalog.yml`. Nothing else changes — the
playbooks loop over that list. [catalog.md](catalog.md) explains the keys.

**Change what a template contains**: the `.j2` files under
`templates/exported/` are generated by `./ise templatize`, so edits there are
lost on the next run. Edit `templates/site/` instead, or the tokens in
`inventory/group_vars/all/04-templating.yml`.

**Change what a step does**: find the role for the command in `roles/` and
read its `tasks/main.yml`. `./ise export` is `roles/ise_export/`, and the file
that handles one resource is `roles/ise_export/tasks/resource.yml`.

## When something breaks

1. Read the task name in the failure — it says which resource.
2. Re-run with `-e resources=<that one>` so you are not waiting on the rest.
3. Add `-vvv` to see the request and ISE's answer in full.
4. [troubleshooting.md](troubleshooting.md) covers the errors that come up.

A failing run leaves the appliance where it got to. Ansible has no rollback,
and neither does ISE — `./ise apply --dry` first is how you avoid needing one.
