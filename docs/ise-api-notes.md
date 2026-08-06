# How ISE's APIs actually behave

Everything here was found by running against real appliances — `labb-ise-001`
on 3.3.0.430 P11 and `laba-ise-004` on 3.5.0.527. Each item cost a failed run.

## Two APIs, because neither one is enough

| | ERS | OpenAPI |
|---|---|---|
| port | 9060 | 443 |
| response envelope | `SearchResult.resources` | `response` |
| wrapper key | yes, one per resource (`NetworkDevice`) | none |
| listings | id, name and link only — every object needs its own GET | whole objects |
| holds | devices, groups, profiles, ACLs, guest, TrustSec | **policy sets**, conditions, repositories, system settings |

Both have to be enabled on the appliance, separately, under *Administration →
System → Settings → API Settings*. `./ise check` tells you whether they are.

## Paging

**ERS caps a page at 100** regardless of what you ask for.

**OpenAPI pages silently, defaulting to 100.** There is no `total`, no
`nextPage`, nothing in the response to say it was cut off — a truncated list
looks exactly like a complete one. `/api/v1/trustsec/sgacl/nbarapp` holds 780
objects and returned exactly 100 until this was caught. Ask for `size` and you
get it, up to a ceiling somewhere under 5000, which is rejected outright.
Export asks for 500, pages on only when the first page comes back full, and
warns if it exhausts `ise_api_max_pages`.

**`total: 0` does not mean empty.** `networkdeviceprofile` and
`certificateprofile` report zero while returning every object they hold, and
ignore `size` entirely — `?size=2` still returns all 9. Deriving a page count
from `total` alone exports nothing at all for them, silently. Export always
fetches page one for that reason.

**Empty is not absent.** Most of the resources added late to this project read
`[]` on the source lab, and an empty collection is indistinguishable from a
missing one unless you go looking. That is the single reason the catalog kept
growing: nothing in an export said "this resource exists and holds nothing".

## Things ISE will not accept back

Export to apply is not a guaranteed round trip. ISE ships objects its own write
validation rejects, and both APIs have write-side rules the read side never
hints at.

**`Blocked List` cannot be written back.** It is a built-in endpoint group with
a space in its name, and PUT answers *"The identity group field must contain
only alphanumeric, dashes, or underscore characters"*. All 35 endpoint groups
on this lab are Cisco built-ins, identical on any appliance, so `templatize_skip`
drops `systemDefined` objects rather than generating templates that cannot be
applied.

**PUT wants the id in the body as well as in the URL**, on both APIs. Omit it
on OpenAPI and you get a 500 reading *"Bad Request. Please see logs for more
details."* — which looks like a server fault rather than a malformed request.

**A condition's `attributeValue` is validated against what it points at.**
`DEVICE:Location = "All Locations#Nowhere"` is rejected outright. That is why
`condition` sits *after* the group resources in the catalog and cannot move
back above them.

**Policy set ranks are a dense sequence.** A new set has to take a rank inside
the existing range — otherwise `Rank=99. Must be in range between 0 and 3`.
Templates carry no rank; apply inserts each new set immediately above
`Default`.

**ERS `DELETE` needs a `Content-Type` header** despite having no body. Without
one: 415, which reads like a malformed body on a request that has none.

**`networkdeviceprofile` is not a supported ERS write resource.** Cisco's own
Python SDK, [`ciscoisesdk`](https://github.com/CiscoISE/ciscoisesdk), which is
generated from ISE's API definitions, has no `network_device_profile` module at
all — checked across every version it ships: `v3_1_0`, `v3_1_1`,
`v3_1_patch_1`, `v3_2_beta`, `v3_3_patch_1`, `v3_5_0`. By contrast
`certificate_profile.py` is there and exposes `create_certificate_profile`,
which matches this lab exactly: `certificateprofile` POST returns 201,
`networkdeviceprofile` POST never succeeds.

The endpoint does respond to POST — it parses a body and complains about
specific fields — which is what makes it look supported. It is not. The write
schema and the read schema do not even agree: `eapTlsLBit` is returned by every
GET and fails deserialization on POST, so a profile ISE produced cannot be sent
back. A deliberately malformed body makes ISE enumerate its accepted property
names, which include `snmpTimeoutSec`, `snmpNadPortDetectionMethod` and `oid` —
none of which appear in any GET — and exclude `description`, `redirectParam`,
`radiusPortBounce`, `radiusReauthLast`, `permTempSetAclEnabled` and
`ciscoProvided`, which always do.

Per-site device profiles have to be made in the GUI. Cisco's own guidance is to
clone an existing NAD profile and customise it rather than build one from
scratch. Export captures the result.

## Not reachable through either API

**Posture.** Conditions, requirements, remediation actions, posture policy,
client provisioning. Not an ERS resource under any spelling, not among the
OpenAPI groups, every candidate path 404s. It is GUI-and-backup only on both
3.3 and 3.5. Nothing here can template it.

**MnT** (`/admin/API/mnt/...`) is live, but it is the monitoring API —
sessions, active counts, failure reasons, read-only XML. There is no
configuration behind it to export or apply.

**Node lifecycle.** The other OpenAPI groups — certificates, deployment, patch,
upgrade, licensing, backup-restore — describe one deployment rather than
portable configuration.

3.5 adds `Profiler`, `Rbac Catalog`, `Prometheus AlertManager` and `Patch and
Upgrade` groups that 3.3 does not have. Still no posture.

## Known edges in this project

**Filenames are derived from object names**, with everything outside
`[A-Za-z0-9._-]` replaced by `_`. Two objects differing only in punctuation
would collide. Nothing in this lab does.

**Apply never deletes.** An object on the target with no template is left
alone, and a rule removed from a template stays on the appliance. Apply
converges the target towards the templates; it does not make them equal.

**A re-export leaves stale files.** Deleting an object upstream does not remove
its template.

**Detail fetches dominate the runtime.** ERS listings carry only id, name and
link, so `profilerprofile` (889 objects), `networkdevice` (504) and
`internaluser` (252) are a GET each. A full export is tens of minutes.
