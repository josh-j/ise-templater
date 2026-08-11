# Troubleshooting

Start with `./ise check`. Most problems are the password, the address, or an
API that was never switched on, and that is what it tests.

## Before a run gets going

**`No ISE password.`**
Nothing has a password to use. Run `./ise setup`, which copies
the `.example` in `inventory/group_vars/all/` to `99-credentials.yml` for you
to fill in.
`ISE_PASSWORD` in the environment works too.

**`ise: no ansible-playbook found. Run './ise setup' first.`**
No environment yet. `./ise setup` builds one. If you have no `uv`, see the
install notes at the end of the README.

**`ERROR! Attempting to decrypt but no vault secrets found`**
The credentials file is ansible-vault encrypted and nothing supplied the vault
password. `./ise` prompts on its own when it notices, so this usually means you
ran `ansible-playbook` directly — add `--ask-vault-pass`, or put the password
in `.vault-pass`, which `./ise` picks up.

**The run says `skipping: no hosts matched`.**
The group in the playbook has no hosts. Check `inventory/hosts.yml`, and check you
did not `--limit` yourself out of the run.

## What `./ise check` tells you

**`401 -- wrong password, or the account is missing the ERS Admin / Super Admin
role`**
The same answer covers both, so check both. The account needs **ERS Admin** for
the ERS resources and **Super Admin** or equivalent for the OpenAPI ones. Also
worth remembering: this is the GUI password, which on ISE is a different
credential from the CLI/SSH one.

**`403 -- the account authenticated but is not allowed this API`**
The password is right and the role is not.

**`no answer`**
Nothing responded. In order of likelihood: the address in `inventory/hosts.yml` is
wrong, the appliance is not up, the API was never enabled (*Administration →
System → Settings → API Settings* — ERS and Open API are separate switches), or
something between you and it is blocking 9060 or 443.

**ERS answers and OpenAPI does not, or the other way round.** They are enabled
separately and this project needs both. Half the catalog lives behind each.

## During an export

**A resource fails with a 404 and the run stops.**
The resource may not exist on that ISE version. Entries that are known to come
and go carry `optional: true` in the catalog; if you have found a new one, that
is the fix.

**An export finishes suspiciously fast, or a collection looks short.**
See the paging notes in [ise-api-notes.md](ise-api-notes.md). A truncated
OpenAPI list looks exactly like a complete one, and `total: 0` does not mean
empty.

**It is just slow.**
It is. ERS listings carry only an id, a name and a link, so every object needs
its own GET — 889 profiler profiles is 889 requests. A full export is tens of
minutes. Narrow it while you are working: `./ise export -e resources=sgt`.

## During an apply

**`Rank=99. Must be in range between 0 and 3`**
Policy set ranks are a dense sequence and a new set has to land inside the
existing range. Apply inserts new sets immediately above `Default`; this error
means something else moved the ranks underneath it.

**`condition.children[1].id, must not be null`**
A policy set references a condition the target does not have under that name.
If the condition is created by the same run, the ordering in the catalog is
what guarantees it exists first — check the condition actually applied earlier
in the output.

**`The identity group field must contain only alphanumeric, dashes, or
underscore characters`**
Something is trying to write back an object whose name ISE will not accept —
`Blocked List` is the built-in example. Those are meant to be filtered out by
`templatize_skip`; if one got through, the template should not exist.

**`Bad Request. Please see logs for more details.` with a 500**
Usually a PUT missing the id in the *body*. Both APIs want it in the body as
well as in the URL.

**A 400 partway through a policy set.**
Something the set references is missing on the target. The preflight is
supposed to catch this before the first write — read the preflight line in the
output, which counts what resolved and what did not.

**`415` on a delete.**
ERS wants a `Content-Type` header even on a request with no body.

**It says `ok` and I cannot tell what it wrote.**
`uri` reports a successful write as `ok`, not `changed` — Ansible cannot tell
whether a POST changed anything. Run `./ise apply --dry` for the CREATE/UPDATE
decision on every object.

## Getting more detail

```sh
./ise apply --dry -e resources=policy-set    # one resource, no writes
./ise export -e resources=sgt -vvv           # full request and response
```

`-vvv` prints the URL, the headers and ISE's answer in full. It is the fastest
way to see what the appliance actually said, as opposed to what Ansible made of
it.

## Nothing here matches

Re-run the one resource that failed with `-vvv`, and read ISE's own message.
The behaviours in [ise-api-notes.md](ise-api-notes.md) were all found that way.
