"""Filters the ISE playbooks need and Jinja cannot express cleanly.

Kept deliberately small. Everything else in this project is stock Ansible.
"""

import re

from ansible.utils.unsafe_proxy import wrap_var


def ise_strip(data, keys):
    """Recursively drop `keys` from every dict in `data`.

    A top-level strip was enough while this only spoke ERS, where `id` and
    `link` sit on the object and nowhere else. Policy sets nest: a rule holds
    a condition, the condition holds its own `id` and `link`, and a rule
    carries `hitCounts` several levels down. All of it is server-owned.
    """
    if isinstance(data, dict):
        return {
            k: ise_strip(v, keys)
            for k, v in data.items()
            if k not in keys
        }
    if isinstance(data, list):
        return [ise_strip(v, keys) for v in data]
    return data


def ise_tokenize(text, tokens):
    """Rewrite site-specific literals as Jinja references, all in one pass.

    This was a loop of `ansible.builtin.replace` tasks, one per file per
    token -- 889 profiler profiles times three tokens is 2,667 module
    invocations to do what is three regex substitutions per string.

    The result is wrapped unsafe: it deliberately contains `{{ ... }}` that
    must survive into the .j2 file rather than being expanded on the way out.
    """
    for token in tokens or []:
        text = re.sub(token["regexp"], token["replace"], text)
    return wrap_var(text)


def ise_policy_refs(doc):
    """Every object a policy set document names but does not contain.

    A policy set is mostly pointers. Applied against an appliance that is
    missing any of them, ISE answers 400 partway through -- after the set has
    been created and some of its rules written. Collecting the references up
    front is what makes it checkable before the first write.

    Returned as {kind, name} so the caller can look each kind up in the right
    place. Kinds match catalog resource names where one exists.
    """
    refs = []

    def add(kind, name):
        if isinstance(name, str) and name and {"kind": kind, "name": name} not in refs:
            refs.append({"kind": kind, "name": name})

    def walk(node):
        if isinstance(node, dict):
            if node.get("conditionType") == "ConditionReference":
                add("condition", node.get("name"))
            if node.get("conditionType") == "ConditionAttributes":
                # A DEVICE condition names a network device group, but by the
                # dictionary's `key` form -- "All Locations#Campus" -- not the
                # group's own name, which carries the root: "Location#All
                # Locations#Campus". Reassemble it so the check compares like
                # with like.
                if node.get("dictionaryName") == "DEVICE" and node.get("attributeValue"):
                    add("networkdevicegroup",
                        "%s#%s" % (node.get("attributeName"), node["attributeValue"]))
            for key, value in node.items():
                if key == "serviceName":
                    add("allowedprotocols", value)
                elif key == "identitySourceName":
                    # Could be an identity store sequence, an AD join point, or
                    # a built-in like "Internal Users". Guess the sequence,
                    # which is the only one this tool can create; if that 404s
                    # the caller downgrades it rather than failing, because the
                    # other two are appliance facilities, not documents.
                    add("idstoresequence", value)
                elif key == "daclName":
                    add("downloadableacl", value)
                elif key == "profileName":
                    add("networkdeviceprofile", value)
                elif key == "certificateAuthenticationProfile":
                    add("certificateprofile", value)
                elif key == "securityGroup":
                    add("sgt", value)
                elif key == "profile" and isinstance(value, list):
                    for item in value:
                        add("authorizationprofile", item)
                elif key == "commands" and isinstance(value, list):
                    for item in value:
                        add("tacacscommandsets", item)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(doc)
    return refs


def ise_reject_field(objects, field, value):
    """Drop objects whose `field` equals `value`, tolerating its absence.

    `rejectattr` raises on the objects that lack the key entirely, which is
    most of them, so it cannot express "skip the Cisco built-ins".
    """
    if not field:
        return objects
    return [o for o in objects if o.get(field) != value]


def ise_swap_ref(obj, mapping, from_field, to_field):
    """Rewrite an id-valued reference into a name-valued one, or back.

    Endpoint groups point at their parent by UUID. That UUID is the last
    un-portable thing in an otherwise name-addressed template set: it works
    between appliances restored from one backup and nowhere else. Templatize
    swaps it id -> name against the export's own contents; apply swaps it
    name -> id against the target's. Direction is decided entirely by which
    mapping and field names are passed.

    A reference that cannot be resolved is left as it is, so it fails loudly
    at the API rather than silently pointing somewhere plausible.
    """
    if not isinstance(obj, dict) or from_field not in obj:
        return obj
    value = obj[from_field]
    if value not in mapping:
        return obj
    out = dict(obj)
    del out[from_field]
    out[to_field] = mapping[value]
    return out


def ise_swap_parent(obj, mapping, spec, to_name=True):
    """Rewrite an object's reference to its own parent, id <-> name.

    `spec` is the catalog's `parent_ref` -- {id_field, name_field} -- or
    nothing at all for the resources that have no parent, which is most of
    them. Absence used to be expressed by passing a field name picked never to
    match ('_none'), which held only for as long as no ISE object had a field
    called `_none`. Here it is just an empty spec.

    `to_name` picks the direction, the same way `ise_swap_refs` does: True for
    templatize (id -> name, against the export's own contents), False for
    apply (name -> id, against the target's).
    """
    if not spec:
        return obj
    if to_name:
        return ise_swap_ref(obj, mapping, spec["id_field"], spec["name_field"])
    return ise_swap_ref(obj, mapping, spec["name_field"], spec["id_field"])


def ise_swap_refs(obj, refs, maps, to_name):
    """Rewrite id-valued references that point at *other* resources.

    `ise_swap_ref` handles one field resolved against its own resource, which
    covers an endpoint group naming its parent. An egress matrix cell is a
    harder shape: three references, two of them SGTs and one a list of
    SGACLs, and nothing else in the object -- a cell IS its references. Get
    them wrong and you have silently bound the wrong security groups.

    `refs` is the catalog's list of {field, name_field, resource, list}.
    `maps` is {resource: mapping}. `to_name` picks the direction: True for
    templatize (id -> name), False for apply (name -> id).

    A reference that cannot be fully resolved is left under its original
    field name, so ISE rejects it rather than the run quietly binding a
    stale id.
    """
    if not isinstance(obj, dict):
        return obj
    out = dict(obj)
    for ref in refs or []:
        source = ref["field"] if to_name else ref["name_field"]
        target = ref["name_field"] if to_name else ref["field"]
        mapping = (maps or {}).get(ref["resource"]) or {}
        if source not in out:
            continue
        value = out[source]
        if ref.get("list"):
            if not isinstance(value, list) or any(v not in mapping for v in value):
                continue
            resolved = [mapping[v] for v in value]
        else:
            if value not in mapping:
                continue
            resolved = mapping[value]
        del out[source]
        out[target] = resolved
    return out


def ise_unresolved_refs(obj, refs, maps, by_name=False):
    """Reference ids in `obj` that none of `maps` can name.

    Collections do not always list everything they hold. The reserved SGT
    "ANY" is fetchable by id and by name but absent from /ers/config/sgt, and
    it is what every default egress cell points at -- so a map built from the
    listing alone misses the single commonest TrustSec reference. These get
    fetched individually and folded back in.
    """
    missing = []
    if not isinstance(obj, dict):
        return missing
    for ref in refs or []:
        # Templatize looks up the id field and wants names back; apply looks
        # up the name field and wants ids. Same shape, opposite direction.
        field = ref.get("name_field") if by_name else ref.get("field")
        if field not in obj:
            continue
        mapping = (maps or {}).get(ref["resource"]) or {}
        values = obj[field] if ref.get("list") else [obj[field]]
        if not isinstance(values, list):
            continue
        for value in values:
            if value in mapping:
                continue
            entry = {"resource": ref["resource"], "id": value}
            if entry not in missing:
                missing.append(entry)
    return missing


def _truthy(value):
    """Is this on? Tolerates the strings the command line produces.

    `-e ise_group_portals=true` arrives as the string "true", and "false"
    arrives as a string that is perfectly truthy in Python. Anything not
    recognisably off counts as on.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "off", "0", "")
    return bool(value)


def ise_enabled_groups(catalog, groups):
    """Catalog entries whose `group` is switched on, in catalog order.

    An entry with no group is always kept, so adding a resource without
    classifying it fails visible rather than silently disappearing.
    """
    return [
        e for e in catalog
        if "group" not in e or _truthy((groups or {}).get(e["group"], True))
    ]


def ise_exclude_config(catalog):
    """Catalog entries that are templated and applied, in catalog order.

    `rejectattr('mode', ...)` cannot express this: Ansible raises on the
    entries that have no `mode` key at all, which is most of them.
    """
    return [e for e in catalog if e.get("mode", "full") != "config"]


def ise_name_of(obj, key):
    """Read an object's name, following a dotted key when it is nested.

    Most resources keep their name at the top level. Global exception rules
    do not -- they are {rule: {name, ...}, profile: [...]}, so the identity
    apply matches on lives one level down.
    """
    node = obj
    for part in str(key).split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def ise_index(items, key="name", value="id"):
    """`{key: value}` for every object in `items`. Name-to-id, mostly.

    Seven callers built this by hand, and each one spelled its source
    expression out twice so that Jinja could zip the two halves together:

        dict(coll.results | map(attribute='json.SearchResult.resources')
             | flatten | map(attribute='name') | zip(
             coll.results | map(attribute='json.SearchResult.resources')
             | flatten | map(attribute='id')))

    The two halves had to stay identical by hand, and nothing enforced it --
    change one and every name silently maps to the wrong object's id, which
    is a wrong-object write rather than an error.

    Both `key` and `value` may be dotted, so rule objects that carry their
    name one level down index like everything else. Objects missing the key
    are skipped rather than indexed under nothing.
    """
    out = {}
    for item in items or []:
        name = ise_name_of(item, key)
        if name is not None:
            out[name] = ise_name_of(item, value)
    return out


def ise_unwrap_collection(result, singleton=False, name=None, more=None):
    """The objects in an OpenAPI collection response, whatever shape it came in.

    Three shapes to reconcile. Most collections wrap their contents in
    `response`; a few -- endpoint-custom-attribute among them -- return a bare
    JSON array with no envelope at all; and a singleton returns one object
    where a collection returns a list, so it is named and boxed into a list of
    one.

    `more` is the later pages, each its own registered uri result. A singleton
    never has any. A non-200 is not an error: `optional` resources legitimately
    404, and export carries on.
    """
    if (result or {}).get("status") != 200:
        return []
    body = (result or {}).get("json")
    if singleton:
        return [dict(body or {}, name=name)]
    objects = list(body if isinstance(body, list) else ((body or {}).get("response") or []))
    for page in more or []:
        objects.extend(((page or {}).get("json") or {}).get("response") or [])
    return objects


# Catalog keys that may be left out of an entry, and what they mean when they
# are. Only keys whose *absence* carries no meaning belong here: `children`,
# `parent_ref`, `refs` and `root` are all tested with `is defined`, so filling
# them in would change which tasks run.
_CATALOG_DEFAULTS = {
    "api": "ers",
    "name_key": "name",
    "lookup": "name",
    "mode": "full",
    "detail": True,
    "optional": False,
    "singleton": False,
    "strip": [],
    "templatize_skip": {},
}


def ise_catalog_defaults(catalog):
    """Fill in each entry's optional keys, once, so nothing restates them.

    These defaults used to be written at the point of use -- `res.api |
    default('ers')` in eight places, `res.name_key | default('name')` in
    twelve, forty-eight in total. Two costs: the catalog's real shape was
    written down nowhere, and two places were free to default the same key
    differently without anything noticing. `res.api` now just works.
    """
    return [dict(_CATALOG_DEFAULTS, **entry) for entry in catalog or []]


def ise_find_id(collection, key, name):
    """The id of the object in `collection` whose `key` equals `name`.

    `key` may be dotted, so this also matches rule objects that carry their
    name one level down. Returns '' when absent, which is how apply decides
    between POST and PUT.
    """
    for item in collection or []:
        if ise_name_of(item, key) == name:
            return item.get("id", "") or ""
    return ""


def ise_sort_objects(objects, key):
    """Sort by `key`, or leave the order alone when the objects have no such
    field.

    Not every ERS resource names its objects. `guestsmtpnotificationsettings`
    is one settings record with an id and no name at all, and Jinja's `sort`
    raises rather than skipping it -- taking the whole export down with it.
    Export order is cosmetic; failing on it is not worth it.
    """
    items = list(objects or [])
    if not items or not all(isinstance(o, dict) and key in o for o in items):
        return items
    return sorted(items, key=lambda o: str(o.get(key) or ""))


def ise_page_range(total, size, minimum=1):
    """The 1-based page numbers needed to walk `total` objects `size` at a time.

    Always at least `minimum` page, and that is the whole point of having this
    written down once. `networkdeviceprofile` and `certificateprofile` report
    `total: 0` while returning every object they hold, so a page count derived
    from `total` alone reads nothing at all for them -- see
    docs/ise-api-notes.md. Export defended against that inline and the apply
    and destroy paths did not, which is the kind of difference that survives
    only until an appliance exercises it.
    """
    size = int(size or 1)
    pages = (int(total or 0) + size - 1) // size
    return list(range(1, max(int(minimum), pages) + 1))


def ise_slug(name):
    """Filename for an object name. ISE names carry '#', '/' and spaces."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))


def ise_rewrite_condition_ids(data, name_to_id):
    """Repoint every named condition reference at the target's own id.

    A policy or rule condition arrives carrying the *source* appliance's
    condition id. That id means nothing on the target, so it is replaced by
    the id the target holds under the same condition name. A name the target
    does not have is left as-is -- so it fails loudly at apply time rather
    than silently binding to whatever the stale id happens to hit.
    """
    if isinstance(data, dict):
        out = {k: ise_rewrite_condition_ids(v, name_to_id) for k, v in data.items()}
        if out.get("conditionType") == "ConditionReference":
            target_id = name_to_id.get(out.get("name"))
            if target_id:
                out["id"] = target_id
        return out
    if isinstance(data, list):
        return [ise_rewrite_condition_ids(v, name_to_id) for v in data]
    return data


class FilterModule(object):
    def filters(self):
        return {
            "ise_strip": ise_strip,
            "ise_exclude_config": ise_exclude_config,
            "ise_enabled_groups": ise_enabled_groups,
            "ise_tokenize": ise_tokenize,
            "ise_policy_refs": ise_policy_refs,
            # ise_swap_ref is deliberately not exported: it is the mechanism
            # both swap filters share, and every playbook reaches it through
            # ise_swap_parent or ise_swap_refs, which know which direction
            # they are going.
            "ise_swap_parent": ise_swap_parent,
            "ise_swap_refs": ise_swap_refs,
            "ise_page_range": ise_page_range,
            "ise_unresolved_refs": ise_unresolved_refs,
            "ise_reject_field": ise_reject_field,
            "ise_slug": ise_slug,
            "ise_name_of": ise_name_of,
            "ise_index": ise_index,
            "ise_unwrap_collection": ise_unwrap_collection,
            "ise_catalog_defaults": ise_catalog_defaults,
            "ise_find_id": ise_find_id,
            "ise_sort_objects": ise_sort_objects,
            "ise_rewrite_condition_ids": ise_rewrite_condition_ids,
        }
