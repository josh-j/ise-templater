# ise-templater

Three Ansible playbooks. Read Cisco ISE configuration out of one appliance,
turn it into templates, push the templates at another.

```
export.yml       ISE  ->  exports/<node>/<resource>.json
templatize.yml   exports/  ->  templates/<resource>/<name>.json.j2
apply.yml        templates/ + site-templates/  ->  ISE
destroy.yml      undoes apply, in reverse dependency order
```

It speaks both configuration APIs ISE exposes, because neither one is enough:

| | ERS | OpenAPI |
|---|---|---|
| port | 9060 | 443 |
| envelope | `SearchResult.resources` | `response` |
| wrapper key | yes, per resource (`NetworkDevice`) | none |
| listing | id/name/link only, needs a detail GET each | whole objects |
| holds | devices, groups, profiles, ACLs, guest, TrustSec | **policy sets**, conditions, repositories, system settings |

Everything is `ansible.builtin.uri`. No collection, no `ciscoisesdk`, no SSH —
`ansible_connection` is `local` for every host and `ansible_host` exists only
to build the URL. The one exception to stock Ansible is `filter_plugins/ise.py`,
about sixty lines doing the three things Jinja cannot say cleanly.

## Setup

`pyproject.toml` plus `uv.lock` — `ansible-core` and what it pulls in, pinned.
Python 3.12+, which is `ansible-core` 2.21's floor rather than ours.

```sh
uv sync
export ISE_PASSWORD=...
```

On a fresh Ubuntu Server 24.04 box, which ships Python 3.12.3:

```sh
sudo apt install -y python3-venv         # or curl -LsSf https://astral.sh/uv/install.sh | sh
python3 -m venv .venv && . .venv/bin/activate && pip install ansible-core
```

Either way it is wheels the whole way down — `cryptography` and `cffi` have
manylinux builds for CPython 3.12, so no compiler and no `-dev` packages. The
`uv` route installs the locked versions; the `venv` route just takes what PyPI
offers, which is fine since nothing here depends on the lockfile.

There is no `sops` step and nothing to install for it. The admin password comes
from `ISE_PASSWORD` in the environment, and the playbooks stop with a message
saying so if it is unset. This is the *UI* password, which on ISE is a separate
credential from the CLI/SSH one.

The sops store is still there for the machine that has it —
`-e ise_password_sops=true` reads `lab_ise_ui_admin_pw` out of
`/srv/nix-config/secrets/common.yaml` — but it is off by default and never
consulted otherwise.

## Use

```sh
uv run ansible-playbook export.yml
uv run ansible-playbook templatize.yml
uv run ansible-playbook apply.yml -e ise_dry_run=true      # look first
uv run ansible-playbook apply.yml
```

Narrow any of them to specific resources:

```sh
uv run ansible-playbook export.yml -e resources=policy-set,condition
```

## Inventory

| Group | Node | Address | Role |
|---|---|---|---|
| `ise_source` | `labb-ise-001` | `10.200.30.10` | read from |
| `ise_target` | `laba-ise-004` | `10.200.30.13` | **written to** |

`apply.yml` runs against `ise_target` and nothing else, so that group is the
whole blast radius. `10.200.30.11` is not wired in (ERS was not answering as of
2026-08-04) and `10.200.30.12` is locked out (admin API returns 401).

## Which families are collected

Every catalog entry carries a `group`, and a group switched off is skipped by
all four playbooks as if it were not in the catalog:

| group | default | what |
|---|---|---|
| `core` | on | devices, groups, profiles, ACLs, conditions, dictionaries |
| `trustsec` | on | SGT/SGACL, egress matrix, VNs, SXP, IP-SGT mappings |
| `deviceadmin` | on | TACACS+ profiles, command sets, policy |
| `identity` | on | LDAP, REST ID stores, RADIUS/TACACS proxying |
| `guest` | on | guest types, locations, sponsor groups, SSIDs |
| `integration` | on | pxGrid, ACI, IPsec, Data Connect, SMS |
| `portals` | **off** | the portal family and themes |
| `runtime` | **off** | endpoints and other session state |
| `lifecycle` | **off** | node groups, trusted certs, session service nodes |

```sh
uv run ansible-playbook export.yml -e ise_group_portals_override=true
```

Off by default is about signal, not capability: portals are large nested
objects with per-node URLs baked in, runtime changes by the second, and
lifecycle belongs to `iselab` rather than to a config template. All three are
modelled, and all three are one flag away.

## What is managed

`ise_catalog` in `group_vars/all.yml`, in apply order — conditions and groups
before the things that reference them. Each entry declares the API it lives
behind and how it misbehaves.

79 resources across both APIs. By family:

- **core** — network devices and groups, endpoint groups, conditions, network
  and time conditions, allowed protocols, dACLs, authorization profiles,
  profiler profiles, filter policies, NSP profiles, certificate templates,
  custom endpoint attributes, dictionaries, policy sets, global exceptions
- **trustsec** — SGTs, SGACLs, the egress matrix, virtual networks, NBAR
  apps, SGT reservation, SGT-VN-VLAN, IP-SGT mappings, SXP
- **deviceadmin** — TACACS+ profiles and command sets, device-admin policy
  sets, global exceptions and conditions
- **identity** — identity sequences, LDAP, REST ID stores, RADIUS and TACACS
  proxying, internal and admin users, AD join points
- **guest** — guest types and locations, sponsor groups, SSIDs, SMTP settings
- **integration** — pxGrid and pxGrid Direct, ACI, IPsec, ANC, Data Connect,
  SMS, the integration catalog
- **portals** *(off)* — the portal family, themes, global settings
- **runtime** *(off)* — endpoints
- **lifecycle** *(off)* — node and node groups, repositories, trusted
  certificates, proxy settings, session service nodes

**Exported only** (`mode: config`) — captured as a record of the appliance,
never templated, never written anywhere:

| resource | why |
|---|---|
| `internaluser` | password state cannot round-trip an export |
| `adminuser` | same, and these are admin credentials |
| `identitygroup` | ERS serves it read-only |
| `activedirectory` | a join point is an operation, not a document |
| `node` | deployment membership; belongs to `iselab` |
| `sxpvpns` | no `name` field, and the listing carries only id and link — nothing to match an object on |
| `repository` | carries backup-target credentials |
| `system-settings-proxy` | node-local |

Endpoints are deliberately absent — session data, not configuration.

### Catalog keys

| key | what |
|---|---|
| `api` | `ers` (default) or `openapi` |
| `root` | ERS wrapper key — not derivable from the path |
| `path` | OpenAPI path |
| `group` | which family it belongs to, for the toggles above |
| `mode` | `full` (default) or `config` (export only) |
| `detail` | false where ERS has no GET-by-id |
| `name_key` | where the name isn't `name`; may be dotted (`rule.name`) |
| `strip` | extra server-owned fields |
| `templatize_skip` | field/value marking objects not worth templating |
| `lookup` | `name` (default) or `collection` |
| `singleton` | endpoint returns one object, not a list |
| `optional` | tolerate 4xx/5xx — the resource may not exist on this version |
| `children` | OpenAPI sub-collections kept in one document |
| `parent_ref` | an id reference to this resource's own objects |
| `refs` | id references to *other* resources, list-valued or not |

None of these are guesswork — each is there because the appliance behaves that
way and the run failed until it was declared:

- `/ers/config/endpointgroup` returns `EndPointGroup`, capital P.
  `activedirectory` returns `ERSActiveDirectory`. The wrapper key is not
  derivable from the path.
- `sxpvpns` has no `name` field anywhere — it's `sxpVpnName`, and the
  collection listing omits it entirely.
- `guestlocation` has no GET-by-id at all; the listing is the whole object.
- **Get-by-name doesn't exist everywhere, and the obvious fallback is a trap.**
  `sgacl`, `guesttype`, `guestlocation`, `sponsorgroup` and `profilerprofile`
  answer **405** to `GET .../name/<name>`. The tempting substitute is
  `?filter=name.EQ.<name>` — but `networkdevicegroup` and
  `authorizationprofile` answer **400** to it, and worse, `guesttype` and
  `sponsorgroup` accept it, **silently ignore it, and return the whole
  collection**. Matching on the first result would have bound every template to
  one object's id. So `lookup: collection` reads the collection once per
  resource and matches in memory; the filter is not used at all.
- `generationId` (SGT/SGACL) and `systemDefined` (endpoint groups) are
  server-owned, on top of the `id`/`link` stripped everywhere.

## Policy sets

The only hierarchical case, and the reason the OpenAPI transport exists. A
policy set is nothing without its rules, and the rules are separate collections
hanging off its id. Export fetches the set and its `authentication`,
`authorization` and `exception` collections and keeps them in **one document**,
because that's the unit a person reasons about and the unit that has to be
applied together:

```json
{ "name": "Lab Wired 802.1X",
  "policySet": {...}, "authentication": [...],
  "authorization": [...], "exception": [] }
```

Two things make this harder than the flat resources:

**Conditions are stored by reference, and the reference carries an id.** That id
is meaningless on the target. Before writing anything, `apply` reads the
target's condition library, builds a name→id map, and repoints every
`ConditionReference` in the document (`ise_rewrite_condition_ids`). A condition
name the target doesn't have is left alone, so it fails loudly rather than
silently binding to whatever the stale id happens to hit.

**`rank` is kept, `hitCounts` is stripped.** Rule order is a decision an admin
made; hit counters are runtime. Rules are applied in rank order so a freshly
created policy set ends up ordered like the source.

ISE creates a default rule with every policy set and rejects a POST of one, so
defaults are only ever updated. If the target has no rule under that name, the
run says so rather than failing.

## How templating works

`templatize.yml` writes one file per object: strips the server-owned fields
**recursively** (policy set rules nest their own ids and hit counts several
levels down), re-wraps ERS objects in their key, and rewrites site-specific
literals into Jinja references using `ise_tokens`:

```yaml
ise_tokens:
  - regexp: 'ise3simprobe'
    replace: !unsafe '{{ site_radius_secret_sim }}'
```

`regexp` is a regular expression, so escape dots you mean literally. `!unsafe`
stops Ansible from expanding the replacement while it's being written — the
braces have to survive into the `.j2` file. The three shipped tokens are the
three distinct RADIUS shared secrets the lab actually uses (500 simulator NADs,
3 lab switches, 1 CML device). Defaults reproduce the source exactly, so an
untouched round trip is a no-op rather than a surprise.

## How apply works

Object **name** is the identity — ids are never carried across, because each
appliance mints its own. Not found means POST, found means PUT.

`-e ise_dry_run=true` prints the CREATE/UPDATE decision for every object and
writes nothing. It's the only readable account of what a run will do: `uri`
reports a successful PUT as `ok`, not `changed`, and nothing diffs an object
before writing it.

## Fanning one template across sites

`templates/` is one file per object — an export of what exists. `site-templates/`
is one file per *kind* of object, rendered once per entry in `sites.yml`:

```sh
uv run ansible-playbook apply.yml -e @sites.yml -e ise_dry_run=true
uv run ansible-playbook apply.yml -e @sites.yml
```

Nothing in the apply path needed changing to support it: object identity already
came from the *rendered* `name`, not the filename. Adding a site is an entry in
`sites.yml`; you never copy a template.

`-e ise_site_only=true` pushes only the fan-out, leaving the exported templates
untouched.

The shipped example builds two sites, New York and London, from nothing — 11
objects each, in dependency order:

```
networkdevicegroup    Location#All Locations#<name>
endpointgroup         <slug>_Corp_Devices
sgt                   <slug>_Corp
condition             <slug>_At_Site          DEVICE:Location
condition             <slug>_Corp_Endpoint    IdentityGroup:Name
downloadableacl       <slug>_Contractor_DACL, <slug>_Quarantine_DACL
authorizationprofile  <slug>_Employee_Full, <slug>_Contractor_Limited, <slug>_Quarantine
policy-set            <name> Wired 802.1X + 4 rules
```

The only things assumed to exist are genuine ISE built-ins: `Wired_802.1X`,
`Network_Access_Authentication_Passed`, `Default Network Access`,
`All_User_ID_Stores`, `DenyAccess`. It deliberately does **not** use the lab's
`ISE_Employees_Group` / `ISE_Contractors_Group` — those are AD external group
conditions needing a `lab.local` join point, which is an operation this tool
cannot perform. Membership is by endpoint identity group instead.

### Dependency preflight

A policy set is mostly pointers. `ise_policy_refs` walks the document and pulls
out everything it names — conditions, authorization profiles, allowed protocols,
identity sources, SGTs, dACLs, TACACS command sets, plus the network device
group hidden inside a `ConditionAttributes`. Each is resolved on the target
*before the first write*:

```
New York Wired 802.1X: 10 references resolved (11 named, 1 not checkable)
```

Without it, a missing profile surfaces as a 400 partway through — after the set
exists and some rules are in — and you reconcile a half-applied policy by hand.

The one not checkable is `identitySourceName`: the value can be an identity
store sequence, an AD join point, or a built-in like `Internal Users`, and no
single endpoint answers for all three. It is declared in
`preflight_unresolvable` and counted separately rather than quietly passed.

In a dry run the preflight *reports* rather than fails, because a dry run
creates nothing and would otherwise trip over prerequisites that same run
would supply.

## Tearing it down

```sh
uv run ansible-playbook destroy.yml -e @sites.yml                      # show only
uv run ansible-playbook destroy.yml -e @sites.yml -e ise_dry_run=false
```

`destroy.yml` is dry by default — the opposite of `apply.yml`. You have to ask
for the writes.

It walks `ise_catalog` **reversed**, and that is the whole point: ISE refuses to
drop an object something else still points at. A dACL cannot go while a profile
names it; that profile cannot go while a policy set rule names it. Deleting in
apply order fails on the first dependency. Deleting a policy set takes its rules
with it, so rules are never deleted individually.

By default it only touches what the *site* templates name. `templates/` is an
export of the appliance's own configuration, much of it Cisco built-ins, so
pointing a delete at it needs `-e ise_site_only=false -e
ise_confirm_destroy_exported=true` and refuses without both.

Verified as a round trip against the lab: build 22 objects, tear down 22, every
resource count back to its baseline.

## Not reachable through either API

Checked against both lab nodes — `labb-ise-001` on 3.3.0.430 P11 and
`laba-ise-004` on 3.5.0.527:

- **Posture.** Conditions, requirements, remediation actions, posture policy,
  client provisioning. Not an ERS resource under any spelling, not among the
  OpenAPI groups, every candidate path 404. It is GUI-and-backup only on both
  versions. Nothing here can template it.
- **MnT** (`/admin/API/mnt/...`) is live but it's the monitoring API — sessions,
  active counts, failure reasons, read-only XML. There is no configuration
  behind it to export or apply.
- The other OpenAPI groups (certificates, deployment, patch, upgrade,
  licensing, backup-restore, …) are node lifecycle rather than portable
  configuration, and that already lives in `iselab` / nix-config.

3.5 adds `Profiler`, `Rbac Catalog`, `Prometheus AlertManager` and
`Patch and Upgrade` groups that 3.3 doesn't have. Still no posture.

## Things ISE will not accept back

Export → apply is not a guaranteed round trip. ISE ships objects its own write
validation rejects, and both APIs have write-side rules the read side never
hints at. Each of these cost a failed run:

- **`Blocked List`**, a built-in endpoint group, has a space in its name. PUT it
  back and you get *"The identity group field must contain only alphanumeric,
  dashes, or underscore characters"*. All 35 endpoint groups here are Cisco
  built-ins, identical on every appliance, so `templatize_skip` drops
  `systemDefined` objects rather than generating templates that cannot be
  applied.
- **PUT wants the id in the body as well as the URL**, on both APIs. Omit it on
  OpenAPI and you get a 500 reading *"Bad Request. Please see logs for more
  details."* — which looks like a server fault, not a malformed request.
- **A condition's `attributeValue` is validated against what it points at.**
  `DEVICE:Location = "All Locations#Nowhere"` is rejected outright, which is why
  `condition` sits *after* the group resources in the catalog and cannot move
  back above them.
- **Policy set ranks are a dense sequence.** A new set must take a rank inside
  the existing range — `Rank=99. Must be in range between 0 and 3`. Templates
  carry no rank; apply inserts each new set immediately above `Default`.
- **ERS `DELETE` needs a `Content-Type` header** despite having no body.
  Without one: 415.
- **`networkdeviceprofile` is not a supported ERS write resource.** Cisco's own
  Python SDK, [`ciscoisesdk`](https://github.com/CiscoISE/ciscoisesdk), which
  is generated from ISE's API definitions, has **no `network_device_profile`
  module at all** — checked across every version it ships: `v3_1_0`, `v3_1_1`,
  `v3_1_patch_1`, `v3_2_beta`, `v3_3_patch_1`, `v3_5_0`. By contrast
  `certificate_profile.py` is present and exposes `create_certificate_profile`,
  which matches this lab: `certificateprofile` POST returns 201,
  `networkdeviceprofile` POST never succeeds.

  The endpoint still responds to POST — it will parse a body and complain
  about specific fields — which is what makes it look supported. It is not.
  The write schema and the read schema do not even agree: `eapTlsLBit` is
  returned by every GET and fails deserialization on POST, so a profile ISE
  produced cannot be sent back. A deliberately malformed body makes ISE
  enumerate its accepted property names, which include `snmpTimeoutSec`,
  `snmpNadPortDetectionMethod` and `oid` — none of which appear in any GET —
  and exclude `description`, `redirectParam`, `radiusPortBounce`,
  `radiusReauthLast`, `permTempSetAclEnabled` and `ciscoProvided`, which
  always do.

  Per-site device profiles have to be made in the GUI. Cisco's own guidance is
  to clone an existing NAD profile and customise it rather than build one from
  scratch. `export.yml` captures the result.
- **`total: 0` does not mean empty.** `networkdeviceprofile` and
  `certificateprofile` report zero while returning every object they hold, and
  ignore `size` entirely (`?size=2` still returns all 9). Deriving a page count
  from `total` alone exports nothing at all for them, silently. Export always
  fetches page one and warns when a full last page suggests truncation.
- **OpenAPI collections page silently, defaulting to 100.** There is no
  `total`, no `nextPage`, nothing in the response to say it was cut off — a
  truncated list looks exactly like a complete one.
  `/api/v1/trustsec/sgacl/nbarapp` holds **780** and returned exactly 100
  until this was caught. Ask for `size` and you get it, up to a ceiling
  somewhere under 5000 (which is rejected outright). Export requests
  `ise_api_page_size` (500), pages on only when the first page fills, and
  warns if it exhausts `ise_api_max_pages`.
- **Empty is not absent.** Most of the resources added late here read `[]` on
  this lab, and an empty collection is indistinguishable from a missing one
  unless you go looking. That is the single reason the catalog kept growing:
  nothing in an export said "this resource exists and holds nothing".
- **The condition name→id map must be read late.** A site template can create
  the very conditions its policy set then references, so a map built at play
  start is missing them and ISE answers *"condition.children[1].id, must not be
  null"*. It is read per-resource, immediately before policy sets are applied.

## Known edges

- **Filenames are derived from object names** with everything outside
  `[A-Za-z0-9._-]` replaced by `_`. Two objects differing only in punctuation
  would collide. Nothing in this lab does.
- **`apply` never deletes.** An object on the target with no template is left
  alone, and a rule you *remove* from a template stays on the appliance. Apply
  converges the target towards the templates; it does not make it equal to
  them. `destroy.yml` is the other half — see below.
- **A re-export leaves stale files.** Deleting an object upstream doesn't remove
  its template.
- **`exports/` is gitignored.** Raw exports contain RADIUS shared secrets, and
  now repository credentials and internal user records, in cleartext. Templates
  are the artefact meant to be committed.
- **Detail fetches dominate the runtime.** ERS listings carry only id/name/link,
  so `profilerprofile` (889), `networkdevice` (504) and `internaluser` (252) are
  a GET each. A full export is tens of minutes.
