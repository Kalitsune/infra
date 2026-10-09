#!/usr/bin/env python
"""Rebuild a SOPS Secret in a new namespace from the live cluster's plaintext.

`metadata.namespace` is covered by the SOPS MAC, so editing it in place
invalidates the file and Flux fails decryption for the whole kustomization.
Without the age private key the existing ciphertext cannot be read back — but
if the Secret is already deployed, the plaintext is readable from the live
cluster, so the file can be rebuilt from scratch and re-encrypted with the
public recipient from .sops.yaml.

Values flow kubectl -> dict -> yaml.dump -> sops -e. They are never printed,
never interpolated into a shell command, and never written outside the repo
path that .sops.yaml's path_regex matches (sops -e fails elsewhere).
"""
import base64
import os
import pathlib
import subprocess
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
NEW_NS = "agents"

# (repo path, live namespace, secret name)
#
# Two shapes live here. Files with `encrypted_regex: ^(data|stringData)$` keep
# their metadata in plaintext, so only the namespace line is wrong. The rest are
# whole-file encrypted — `kind` and `metadata` are ciphertext too, so the stale
# `namespace: hermes` is unreadable and unfixable in place, and Flux would apply
# them straight back into `hermes` while the workloads wait in `agents` for
# Secrets that never arrive. Both shapes are rebuilt the same way; the output is
# always partially-encrypted so the namespace stays auditable from the repo.
TARGETS = [
    ("kubernetes/apps/agents/umami/matrix-secret.yaml", "hermes", "hermes-matrix-secrets-default"),
    ("kubernetes/apps/agents/hale/matrix-secret.yaml", "hermes", "homelab-expert-matrix-secrets"),
    ("kubernetes/apps/agents/finnegan/a2a-secret.yaml", "hermes", "finnegan-a2a"),
    ("kubernetes/apps/agents/dashboard/oidc-secret.yaml", "hermes", "agents-dashboard-oidc"),
    ("kubernetes/apps/agents/secret.yaml", "hermes", "hermes-secrets"),
    ("kubernetes/apps/agents/hale/secret.yaml", "hermes", "hermes-infra-expert-secrets"),
    ("kubernetes/apps/agents/hale/a2a-secret.yaml", "hermes", "homelab-expert-a2a"),
    ("kubernetes/apps/agents/umami/secrets.yaml", "hermes", "hermes-secret"),
    ("kubernetes/apps/agents/umami/a2a-secret.yaml", "hermes", "hermes-a2a"),
    ("kubernetes/apps/agents/umami/oauth2-secret.yaml", "hermes", "oauth2-proxy-secret"),
]

# Secret names the HelmReleases reference, where the live name no longer matches
# the new agent identity. Renaming the Secret and its references together is
# safe; renaming only one side is what leaves a pod stuck on a missing Secret.
RENAME = {
    "homelab-expert-a2a": "hale-a2a",
    "homelab-expert-matrix-secrets": "hale-matrix-secrets",
}


def live_plaintext(ns, name):
    """Return {key: str} decoded from the live Secret. Never printed."""
    out = subprocess.run(
        ["kubectl", "-n", ns, "get", "secret", name, "-o", "json"],
        capture_output=True, text=True, check=True,
    ).stdout
    import json
    data = json.loads(out).get("data") or {}
    return {k: base64.b64decode(v).decode("utf-8") for k, v in data.items()}


def rebuild(relpath, ns, name):
    dest = REPO / relpath
    old = yaml.safe_load(dest.read_text())
    sops_meta = old.get("sops") or {}
    regex = sops_meta.get("encrypted_regex")
    whole_file = regex is None

    values = live_plaintext(ns, name)
    if not values:
        return f"SKIP {relpath}: live Secret {ns}/{name} has no data"

    if whole_file:
        # metadata and kind are ciphertext, so the repo's key set is readable
        # but its names are not encrypted — compare those.
        old_keys = set(k for k in (old.get("stringData") or old.get("data") or {}))
    else:
        if regex != "^(data|stringData)$":
            return f"SKIP {relpath}: encrypted_regex={regex!r}, refusing to guess"
        old_keys = set((old.get("stringData") or old.get("data") or {}).keys())

    # Preserve the key set exactly: a dropped key silently unconfigures the app.
    if old_keys != set(values):
        return (f"SKIP {relpath}: key mismatch repo={sorted(old_keys)} "
                f"live={sorted(values)}")

    new_name = RENAME.get(name, name)
    doc = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": new_name, "namespace": NEW_NS},
        "type": "Opaque" if whole_file else old.get("type", "Opaque"),
        "stringData": values,
    }

    os.umask(0o077)
    # Must keep a .yaml extension: sops picks its input store by file
    # extension, and an unknown one is treated as binary — it then encrypts
    # the entire document into a single JSON `data` field, ignoring
    # --encrypted-regex and losing metadata.namespace entirely.
    tmp = dest.with_name(dest.stem + ".rebuilt.yaml")
    with open(tmp, "w") as fh:
        yaml.safe_dump(doc, fh, default_flow_style=False, sort_keys=False)
    os.chmod(tmp, 0o600)

    r = subprocess.run(
        ["sops", "-e", "--encrypted-regex", "^(data|stringData)$", "-i", str(tmp)],
        capture_output=True, text=True, cwd=REPO,
    )
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: sops -e: {r.stderr.strip()[:200]}"

    body = tmp.read_text()
    for v in values.values():
        if v and v in body:
            tmp.unlink(missing_ok=True)
            return f"FAIL {relpath}: plaintext value present after encryption"
    if "ENC[AES256_GCM" not in body:
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: no ciphertext in output"

    # Guard the binary-store failure mode: metadata must survive as plaintext
    # and every value must be individually encrypted.
    check = yaml.safe_load(body)
    if not isinstance(check, dict) or check.get("kind") != "Secret":
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: output is not a plaintext-metadata Secret"
    if (check.get("metadata") or {}).get("namespace") != NEW_NS:
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: metadata.namespace not readable as {NEW_NS}"
    if (check.get("metadata") or {}).get("name") != new_name:
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: metadata.name not readable as {new_name}"
    enc_vals = check.get("stringData") or {}
    if set(enc_vals) != set(values):
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: stringData keys changed"
    if not all(str(v).startswith("ENC[AES256_GCM") for v in enc_vals.values()):
        tmp.unlink(missing_ok=True)
        return f"FAIL {relpath}: some values left unencrypted"

    tmp.replace(dest)
    rename = f" (renamed from {name})" if new_name != name else ""
    shape = "whole-file" if whole_file else "partial"
    return (f"OK   {relpath}: {len(values)} key(s) -> {NEW_NS}/{new_name}"
            f"{rename} [was {shape}]")


if __name__ == "__main__":
    bad = False
    for relpath, ns, name in TARGETS:
        line = rebuild(relpath, ns, name)
        print(line)
        if not line.startswith("OK"):
            bad = True
    sys.exit(1 if bad else 0)
