#!/usr/bin/env bash
# Run a playbook inside the container build environment.
#
#   ./run.sh export.yml
#   ./run.sh apply.yml -e ise_dry_run=true
#   ./run.sh shell                          # interactive, toolchain on PATH
#
# The image is built on first use and reused after that. Force a rebuild with
# ISE_REBUILD=1, or just `docker rmi ise-templater`.
#
# ISE_ENGINE overrides the container command and may carry arguments, so
# `ISE_ENGINE="sudo docker" ./run.sh export.yml` works if your user is not in
# the docker group.
set -euo pipefail

image=${ISE_IMAGE:-ise-templater}
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

if [ -n "${ISE_ENGINE:-}" ]; then
  read -ra engine <<<"$ISE_ENGINE"
elif command -v podman >/dev/null 2>&1; then
  engine=(podman)
elif command -v docker >/dev/null 2>&1; then
  engine=(docker)
else
  echo "run.sh: neither podman nor docker on PATH" >&2
  exit 1
fi

if ! "${engine[@]}" info >/dev/null 2>&1; then
  echo "run.sh: '${engine[*]}' cannot reach a container daemon." >&2
  echo "        Add yourself to the docker group, or set ISE_ENGINE=\"sudo docker\"." >&2
  exit 1
fi

if [ -n "${ISE_REBUILD:-}" ] || ! "${engine[@]}" image inspect "$image" >/dev/null 2>&1; then
  echo "run.sh: building $image" >&2
  "${engine[@]}" build -t "$image" "$repo"
fi

# The sops store lives outside the repo, and so does the age key that opens
# it. Both go in read-only; neither is ever written to.
sops_dir=${ISE_SOPS_DIR:-/srv/nix-config/secrets}
age_key=${SOPS_AGE_KEY_FILE:-$HOME/.config/sops/age/keys.txt}

mounts=(-v "$repo:/work")
if [ -d "$sops_dir" ]; then mounts+=(-v "$sops_dir:$sops_dir:ro"); fi
if [ -f "$age_key" ]; then mounts+=(-v "$age_key:/secrets/age-keys.txt:ro"); fi

# Host networking: every task is an HTTPS call to an ISE appliance on the lab
# network, so the container wants the host's routing table, not a NAT bridge.
# --user keeps exports/ and templates/ owned by you rather than by root.
opts=(--rm --network host --user "$(id -u):$(id -g)" "${mounts[@]}")
if [ -t 0 ]; then opts+=(-it); fi

# ISE_PASSWORD overrides the sops lookup -- see group_vars/all.yml.
if [ -n "${ISE_PASSWORD:-}" ]; then opts+=(-e "ISE_PASSWORD=$ISE_PASSWORD"); fi

if [ "${1:-}" = "shell" ]; then
  shift
  exec "${engine[@]}" run "${opts[@]}" --entrypoint bash "$image" "$@"
fi

exec "${engine[@]}" run "${opts[@]}" "$image" "$@"
