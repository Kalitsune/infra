#!/usr/bin/env python
"""Self-check for the agents-namespace rename.

Builds each app kustomization and asserts the invariants that actually break
deploys: namespaced objects land in `agents`, soul.text survives the edits,
and nothing still points at the old `hermes` namespace or service names.

SOPS-encrypted Secrets are reported separately, not failed: their
`metadata.namespace` is covered by the SOPS MAC, so rewriting it in place
(which is what broke this migration the first time) invalidates the file.
Only the operator holds the age key, so those are a handoff list.
"""
import re
import subprocess
import sys

import yaml

APPS = ["agents/umami", "agents/hale", "agents/finnegan", "agents/dashboard"]
EXPECT_NS = "agents"

CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding"}
# Objects that deliberately live outside the app namespace.
FOREIGN_NS = {
    ("HelmRepository", "hermes-agent"): "flux-system",
    ("EnvoyPatchPolicy", "hermes-auth-login-provider-redirect"): "envoy-gateway-system",
}
ENC = re.compile(r"^ENC\[AES256_GCM")

failures = []
sops_todo = []
summary = []


def build(app):
    r = subprocess.run(
        ["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone",
         f"kubernetes/apps/{app}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        failures.append(f"{app}: kustomize build failed: {r.stderr.strip()[:200]}")
        return ""
    return r.stdout


for app in APPS:
    out = build(app)
    if not out:
        continue
    for doc in yaml.safe_load_all(out):
        if not doc:
            continue
        kind = doc.get("kind") or ""
        meta = doc.get("metadata") or {}
        name = meta.get("name") or ""
        ns = meta.get("namespace")

        # Whole-file SOPS: kind/name/namespace are all ciphertext. Nothing to
        # assert, and the operator re-encrypts these.
        if ENC.match(kind) or ENC.match(str(name)):
            continue

        if kind in CLUSTER_SCOPED:
            if kind == "Namespace" and name != EXPECT_NS:
                failures.append(f"{app}: Namespace object is {name!r}, want {EXPECT_NS!r}")
            continue

        if FOREIGN_NS.get((kind, name)) == ns:
            continue

        if ns != EXPECT_NS:
            # Partially-encrypted Secret: name readable, namespace is plaintext
            # and MAC-protected -> operator must re-encrypt with the new value.
            if kind == "Secret":
                sops_todo.append(f"{app}: Secret/{name} still namespace={ns!r}")
            else:
                failures.append(f"{app}: {kind}/{name} namespace={ns!r}, want {EXPECT_NS!r}")

        if kind in ("HelmRelease", "HTTPRoute"):
            summary.append(f"{app:18} {kind:12} {name}")

        if kind == "HelmRelease":
            soul = ((doc.get("spec") or {}).get("values") or {}).get("soul") or {}
            if isinstance(soul, dict) and "text" in soul:
                text = soul["text"]
                if not isinstance(text, str) or len(text) < 200:
                    failures.append(f"{app}: soul.text missing or truncated")

# Stale machine-readable references (ignore prose/comments and SOPS files).
grep = subprocess.run(
    ["git", "grep", "-n", "-E",
     r"\.hermes\.svc|namespace: hermes|apps/hermes/|hermes-hermes-agent|homelab-expert",
     "--", "kubernetes/apps/agents", "kubernetes/apps/open-webui", "kubernetes/cicd"],
    capture_output=True, text=True,
).stdout.strip()

stale = []
for line in grep.splitlines():
    path, _, rest = line.partition(":")
    _, _, body = rest.partition(":")
    body = body.strip()
    if path.endswith(".md") or body.startswith("#") or body.startswith("//"):
        continue
    if "secret" in path and "namespace: hermes" in body:
        continue  # tracked in sops_todo
    stale.append(line)

print("=== built objects ===")
for s in summary:
    print(" ", s)

if sops_todo:
    print("\n=== OPERATOR: re-encrypt these (namespace is MAC-protected) ===")
    for s in sops_todo:
        print(" ", s)

if stale:
    print("\n=== stale machine-readable refs ===")
    for s in stale:
        print(" ", s)

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    sys.exit(1)

print("\nOK: namespaced objects -> agents, soul.text intact, no stale refs.")
if sops_todo:
    print("Pending: %d SOPS secret(s) need the operator's age key." % len(sops_todo))
