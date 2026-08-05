# The toolchain the playbooks need, and nothing else: ansible-core, sops to
# read the admin password out of the nix-config secrets store, age because
# that is what those secrets are encrypted to, and jq for poking at exports.
#
# The repo itself is not baked in -- run.sh mounts it. Exports and rendered
# templates are meant to land in your working tree, not inside an image.
FROM python:3.13-slim

ARG TARGETARCH

ARG ANSIBLE_CORE_VERSION=2.21.2
ARG SOPS_VERSION=3.13.3
ARG AGE_VERSION=1.3.1

# Pinned by digest rather than by a checksums file fetched at build time: a
# checksum you download from the same place as the binary proves very little.
ARG SOPS_SHA256_amd64=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b
ARG SOPS_SHA256_arm64=53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77
ARG AGE_SHA256_amd64=bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377
ARG AGE_SHA256_arm64=c6878a324421b69e3e20b00ba17c04bc5c6dab0030cfe55bf8f68fa8d9e9093a

SHELL ["/bin/sh", "-euxc"]

RUN apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl jq; \
    rm -rf /var/lib/apt/lists/*

RUN case "$TARGETARCH" in \
      amd64) sops_sha="$SOPS_SHA256_amd64"; age_sha="$AGE_SHA256_amd64" ;; \
      arm64) sops_sha="$SOPS_SHA256_arm64"; age_sha="$AGE_SHA256_arm64" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    \
    curl -fsSL -o /usr/local/bin/sops \
      "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.${TARGETARCH}"; \
    echo "${sops_sha}  /usr/local/bin/sops" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/sops; \
    \
    curl -fsSL -o /tmp/age.tgz \
      "https://github.com/FiloSottile/age/releases/download/v${AGE_VERSION}/age-v${AGE_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    echo "${age_sha}  /tmp/age.tgz" | sha256sum -c -; \
    tar -xzf /tmp/age.tgz -C /tmp; \
    install -m 0755 /tmp/age/age /tmp/age/age-keygen /usr/local/bin/; \
    rm -rf /tmp/age /tmp/age.tgz

# ansible-core only. The playbooks use builtin modules exclusively, so there
# is no collection and no ciscoisesdk to install.
RUN pip install --no-cache-dir "ansible-core==${ANSIBLE_CORE_VERSION}"

# run.sh runs the container as the invoking uid:gid so that files written into
# exports/ and templates/ belong to you rather than to root. That uid has no
# passwd entry and no home, hence HOME=/tmp and the explicit temp dirs --
# ansible will not fall back to a writable location on its own.
ENV HOME=/tmp \
    ANSIBLE_CONFIG=/work/ansible.cfg \
    ANSIBLE_LOCAL_TEMP=/tmp/.ansible/tmp \
    ANSIBLE_REMOTE_TEMP=/tmp/.ansible/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    SOPS_AGE_KEY_FILE=/secrets/age-keys.txt

WORKDIR /work
ENTRYPOINT ["ansible-playbook"]
CMD ["--help"]
