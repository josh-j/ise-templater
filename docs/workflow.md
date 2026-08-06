# The workflow, step by step

Four steps, in order. Each one has a playbook and a `./ise` command.

```
./ise export       ISE  ->  exports/<node>/<resource>.json
./ise templatize   exports/  ->  templates/<resource>/<name>.json.j2
./ise apply        templates/  ->  ISE
./ise destroy      undoes an apply, in reverse order
```

## 1. Export

```sh
./ise export
./ise export -e resources=policy-set,condition    # just these
./ise export --with portals                       # add an off-by-default family
```

Reads every resource in the catalog from the appliances in `ise_source` and
writes one JSON file per resource to `exports/<node>/`.

**It is slow — tens of minutes for a full run.** ERS collection listings carry
only an id, a name and a link, so every object needs its own GET:
`profilerprofile` is 889 of them, `networkdevice` 504, `internaluser` 252. That
is the runtime, and there is no way around it on that API.

`exports/` is gitignored. Raw exports hold RADIUS shared secrets, repository
credentials and internal user records in cleartext.

## 2. Templatize

```sh
./ise templatize
```

Turns each exported object into its own file under
`templates/<resource>/<name>.json.j2`. Three things happen on the way:

**Server-owned fields are stripped, recursively.** `id` and `link` describe
where an object lives on one appliance; the target mints its own. Recursion
matters because policy set rules nest ids and hit counters several levels down.
Per-resource extras are declared in the catalog: `generationId` on SGTs and
SGACLs, `systemDefined` on endpoint groups, `hitCounts` on policy sets.

**ERS objects are re-wrapped in their key.** ERS answers with
`{"NetworkDevice": {...}}` and expects the same shape back.

**Site-specific literals become variables.** `ise_tokens` in
`group_vars/all/04-templating.yml` is a list of regular expressions and what to
replace them with:

```yaml
ise_tokens:
  - regexp: 'ise3simprobe'
    replace: !unsafe '{{ site_radius_secret_sim }}'
```

`regexp` is a regular expression, so escape any dot you mean literally.
`!unsafe` stops Ansible expanding the replacement while the file is being
written — the braces have to survive into the `.j2` file. The three shipped
tokens are the three distinct RADIUS shared secrets the source lab uses (500
simulator NADs, 3 lab switches, 1 CML device), and their defaults reproduce the
source exactly, so an untouched round trip is a no-op rather than a surprise.

This step works entirely off `exports/`, except when it needs to resolve a
reference the listing did not carry, so it mostly runs offline.

## 3. Apply

```sh
./ise apply --dry            # show the decisions, write nothing
./ise apply
./ise apply -e resources=authorizationprofile
```

Renders each template and writes it to the appliances in `ise_target`.

**Object name is the identity.** Apply looks for an object of that name on the
target: not found means POST, found means PUT. Ids are never carried across.

**`--dry` is the readable account of a run.** It prints the CREATE or UPDATE
decision for every object and writes nothing. It is worth the extra pass,
because `uri` reports a successful PUT as `ok` rather than `changed`, and
nothing diffs an object before writing it — a real run does not tell you much.

**Apply never deletes.** An object on the target with no template is left
alone, and a rule you remove from a template stays on the appliance. Apply
converges the target towards the templates; it does not make the two equal.
`./ise destroy` is the other half.

### Policy sets

The only hierarchical case, and the reason this project speaks the OpenAPI
transport at all. A policy set is nothing without its rules, and the rules are
separate collections hanging off the set's id. Export fetches the set and its
`authentication`, `authorization` and `exception` collections and keeps them in
one document, because that is the unit a person reasons about and the unit that
has to be applied together:

```json
{ "name": "Lab Wired 802.1X",
  "policySet": {...}, "authentication": [...],
  "authorization": [...], "exception": [] }
```

Two things make this harder than the flat resources:

**Conditions are stored by reference, and the reference carries an id** that is
meaningless on the target. Before writing anything, apply reads the target's
condition library, builds a name-to-id map, and repoints every
`ConditionReference` in the document. A condition name the target does not have
is left alone, so it fails loudly rather than silently binding to whatever the
stale id happens to hit.

That map has to be read late. A site template can create the very conditions
its policy set then references, so a map built at the start of the play is
missing them and ISE answers *"condition.children[1].id, must not be null"*. It
is read per-resource, immediately before policy sets are applied.

**`rank` is kept, `hitCounts` is stripped.** Rule order is a decision an admin
made; hit counters are runtime. Rules are applied in rank order, so a freshly
created policy set ends up ordered like the source.

ISE creates a default rule with every policy set and rejects a POST of one, so
default rules are only ever updated. If the target has no rule under that name,
the run says so rather than failing.

### Dependency preflight

A policy set is mostly pointers. Before the first write, everything the
document names — conditions, authorization profiles, allowed protocols,
identity sources, SGTs, dACLs, TACACS command sets, and the network device
group hidden inside a `ConditionAttributes` — is resolved on the target:

```
New York Wired 802.1X: 10 references resolved (11 named, 1 not checkable)
```

Without it, a missing profile surfaces as a 400 partway through, after the set
exists and some of its rules are in, and you reconcile a half-applied policy by
hand.

The one not checkable is `identitySourceName`: the value can be an identity
store sequence, an AD join point, or a built-in like `Internal Users`, and no
single endpoint answers for all three. It is counted separately rather than
quietly passed.

In a dry run the preflight reports rather than fails, because a dry run creates
nothing and would otherwise trip over prerequisites that the same run would
have supplied.

## Fanning one template across sites

`templates/` is one file per object — an export of what exists.
`site-templates/` is one file per *kind* of object, rendered once per entry in
`sites.yml`:

```sh
./ise sites --dry
./ise sites
./ise sites other-sites.yml        # any sites file, not just sites.yml
```

Adding a site is an entry in `sites.yml`. You never copy a template.

Nothing in the apply path needed changing to support this: object identity
already came from the *rendered* name, not the filename.

`./ise sites` passes `-e ise_site_only=true` for you, so it pushes only the
fan-out and leaves the exported templates alone. Add `-e ise_site_only=false`
to the same line to push both.

The shipped example builds two sites, New York and London, from nothing — 11
objects each, in dependency order:

```
networkdevicegroup    Location#All Locations#<name>
endpointgroup         <slug>_Corp_Devices
sgt                   <slug>_Corp
condition             <slug>_At_Site          DEVICE:Location
condition             <slug>_Corp_Endpoint    IdentityGroup:Name
downloadableacl       <slug>_Contractor_DACL, <slug>_Quarantine_DACL
authorizationprofile  <slug>_Employee_Full, <slug>_Contractor_Limited,
                      <slug>_Quarantine
policy-set            <name> Wired 802.1X + 4 rules
```

The only things it assumes already exist are genuine ISE built-ins:
`Wired_802.1X`, `Network_Access_Authentication_Passed`, `Default Network
Access`, `All_User_ID_Stores`, `DenyAccess`. It deliberately does not use the
source lab's AD groups — those need a `lab.local` join point, which is an
operation this project cannot perform. Membership is by endpoint identity group
instead.

## 4. Destroy

```sh
./ise destroy                      # show what would go
./ise destroy --force              # actually delete
```

**Destroy only shows unless you add `--force`** — the opposite of apply, which
writes unless you add `--dry`. Deletes have to be asked for.

It walks the catalog **backwards**, and that is the whole point: ISE refuses to
drop an object something else still points at. A dACL cannot go while an
authorization profile names it, and that profile cannot go while a policy set
rule names it. Deleting in apply order fails on the first dependency; deleting
in reverse works because the referrer is always gone first. Deleting a policy
set takes its rules with it, so rules are never deleted individually.

By default it only touches what the *site* templates name. `templates/` is an
export of the appliance's own configuration, much of it Cisco built-ins, so
pointing a delete at it needs both `-e ise_site_only=false` and `-e
ise_confirm_destroy_exported=true`, and refuses without both.

Verified as a round trip against the lab: build 22 objects, tear down 22, every
resource count back to its baseline.
