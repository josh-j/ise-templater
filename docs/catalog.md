# The catalog

`ise_catalog` in `inventory/group_vars/all/03-catalog.yml` is the list of
everything this project knows how to move: 79 resources across ISE's two APIs.

**List order is apply order.** Groups and conditions come before the objects
that reference them. Export and templatize walk the list forwards; destroy
walks it backwards.

Adding a resource is adding an entry. Nothing else changes — the playbooks loop
over the list.

## Families

Every entry belongs to a family, and a family switched off in
`inventory/group_vars/all/02-families.yml` is skipped by all four playbooks as
if those resources were not in the catalog.

| family | default | what is in it |
|---|---|---|
| `core` | on | network devices and groups, endpoint groups, conditions, network and time conditions, allowed protocols, dACLs, authorization profiles, profiler profiles, filter policies, NSP profiles, certificate templates, custom endpoint attributes, dictionaries, policy sets, global exceptions |
| `trustsec` | on | SGTs, SGACLs, the egress matrix, virtual networks, NBAR apps, SGT reservation, SGT-VN-VLAN, IP-to-SGT mappings, SXP |
| `deviceadmin` | on | TACACS+ profiles and command sets, device-admin policy sets, global exceptions and conditions |
| `identity` | on | identity sequences, LDAP, REST ID stores, RADIUS and TACACS proxying, internal and admin users, AD join points |
| `guest` | on | guest types and locations, sponsor groups, SSIDs, SMTP settings |
| `integration` | on | pxGrid and pxGrid Direct, ACI, IPsec, ANC, Data Connect, SMS, the integration catalog |
| `portals` | **off** | the portal family, themes, global settings |
| `runtime` | **off** | endpoints |
| `lifecycle` | **off** | nodes and node groups, repositories, trusted certificates, proxy settings, session service nodes |

Turn one on for a run:

```sh
./ise export --with portals
./ise export --with portals,runtime --without guest
```

The three that are off are about signal, not capability. Portals are large
nested objects with per-node URLs baked into them; runtime state changes by the
second; lifecycle describes one deployment rather than a portable
configuration. All three are fully modelled and all three are one flag away.

## Exported but never applied

Some resources are captured as a record of the appliance and go no further.
They carry `mode: config`, which means export reads them and templatize and
apply skip them.

| resource | why |
|---|---|
| `internaluser` | password state cannot round-trip an export |
| `adminuser` | the same, and these are admin credentials |
| `identitygroup` | ERS serves it read-only |
| `activedirectory` | a join point is an operation, not a document |
| `node` | deployment membership, not portable configuration |
| `sxpvpns` | no `name` field, and the listing carries only an id and a link — nothing to match an object on |
| `repository` | carries backup-target credentials |
| `system-settings-proxy` | node-local |
| `networkdeviceprofile` | ERS will not accept one back — see [ise-api-notes.md](ise-api-notes.md) |

Endpoints are in the catalog but in the `runtime` family, off by default:
session data, not configuration.

## The keys

Everything except `resource` is optional.

| key | what it does |
|---|---|
| `resource` | the name you type in `-e resources=...` |
| `group` | which family it belongs to |
| `api` | `ers` (the default) or `openapi` |
| `root` | the ERS wrapper key |
| `path` | the OpenAPI path. Ignored for ERS |
| `mode` | `full` (the default), or `config` for export-only |
| `detail` | `false` where ERS has no GET-by-id |
| `name_key` | the field holding the name, where it is not `name`. May be dotted, as in `rule.name` |
| `strip` | extra server-owned fields, on top of `ise_strip_keys` |
| `lookup` | `name` (the default) or `collection` |
| `singleton` | the endpoint returns one object, not a list |
| `optional` | tolerate a 4xx/5xx: the resource may not exist on this ISE version |
| `children` | OpenAPI sub-collections kept in the same document |
| `parent_ref` | an id reference to this resource's own objects |
| `refs` | id references to *other* resources, list-valued or not |
| `templatize_skip` | a field and value marking objects not worth templating |

The defaults above are filled in once, by `ise_catalog_defaults` in
`filter_plugins/ise.py`, as each run works out which resources it touches. So
a task reads `res.api` rather than restating `res.api | default('ers')`, and
adding a key with a default means adding it to `_CATALOG_DEFAULTS` there.

Four keys are deliberately **not** given defaults — `children`, `parent_ref`,
`refs` and `root`. Their absence is meaningful: tasks branch on
`res.children is defined`, so filling them in would change which tasks run.

## Why each key exists

None of them are guesswork. Each is here because an appliance behaved a certain
way and a run failed until it was declared.

**`root` — the ERS wrapper key is not derivable from the path.**
`/ers/config/endpointgroup` returns `EndPointGroup`, with a capital P.
`activedirectory` returns `ERSActiveDirectory`. There is no rule; it has to be
written down per resource.

**`name_key` — not everything calls its name `name`.** `sxpvpns` has no `name`
field anywhere: it is `sxpVpnName`, and the collection listing omits it
entirely. Global exception rules keep their name one level down, at
`rule.name`, which is why the key may be dotted.

**`detail: false` — not everything has a GET-by-id.** `guestlocation` has none
at all; its listing is the whole object.

**`lookup: collection` — get-by-name does not exist everywhere, and the obvious
fallback is a trap.** `sgacl`, `guesttype`, `guestlocation`, `sponsorgroup` and
`profilerprofile` answer **405** to `GET .../name/<name>`. The tempting
substitute is `?filter=name.EQ.<name>` — but `networkdevicegroup` and
`authorizationprofile` answer **400** to it, and worse, `guesttype` and
`sponsorgroup` accept it, silently ignore it, and return the whole collection.
Matching on the first result would have bound every template to one object's
id. So `lookup: collection` reads the collection once per resource and matches
in memory, and the filter is not used at all.

**`strip` — some fields are server-owned beyond `id` and `link`.**
`generationId` on SGTs and SGACLs is bumped by ISE on every write.
`systemDefined` on endpoint groups is ISE's to decide, and it rejects being
told. `hitCounts` on policy sets is a runtime counter.

**`refs` and `parent_ref` — some objects are nothing but references.** 30 of
35 endpoint groups point at a parent by UUID, which is the last un-portable
thing in an otherwise name-addressed template set. An egress matrix cell is
harder still: three references, two SGTs and a list of SGACLs, and nothing
else — a cell *is* its references, and getting them wrong silently binds the
wrong security groups. Both are rewritten to names on the way into a template
and back to the target's own ids on the way out.

**`optional` — not every resource exists on every version.**
`/api/v1/sgt/reservation` is there on 3.5 and 404s on 3.3 P11. `ipsec` 404s
until an IPsec node exists.

**`templatize_skip` — some objects ISE ships cannot be written back.**
`Blocked List`, a built-in endpoint group, has a space in its name, and ISE's
own PUT validator rejects names with spaces. `ANY-ANY`, the egress matrix
catch-all, returns both `defaultRule` and `sgacls` but accepts only one of
them.

**`children` — policy sets are not flat.** The rules hang off the set's id as
separate collections and are kept in one document with it. See
[workflow.md](workflow.md).
