# host_vars

Per-appliance overrides. A file named after a host in `inventory.yml` applies
to that host and no other, and beats anything in `group_vars/all/`.

Use it when one appliance is not like the others — most often a different
admin account:

```yaml
# host_vars/laba-ise-004.yml
ise_user: apiadmin
ise_password: "{{ lookup('env', 'ISE_TARGET_PASSWORD') }}"
```

or a node on a non-default port, or one whose certificate is real:

```yaml
# host_vars/labb-ise-001.yml
ise_validate_certs: true
ise_timeout: 120
```

A password put here in cleartext is committable by accident — this directory
is **not** gitignored. Either `ansible-vault encrypt` the file, or keep the
password in `group_vars/all/99-credentials.yml` and put only the non-secret
settings here.
