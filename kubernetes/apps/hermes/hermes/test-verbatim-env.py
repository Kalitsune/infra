#!/usr/bin/env python3
"""Regression test: verbatim profile env must NOT be base64-encoded.

seed-profiles writes per-profile SECRET files base64-encoded into .env
(correct for multi-line blobs like a kubeconfig, which bin/env.sh decodes by
hand). Anything Hermes reads directly — MATRIX_ALLOWED_USERS and friends —
must instead land VERBATIM, or authorization compares against base64 garbage
and silently denies every sender.

This runs the REAL script from the BUILT manifest against a sandbox and
asserts both behaviours at once.

Usage: test-verbatim-env.py <built.yaml>
"""
import base64
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

PATH_MAP = [
    ("/opt/hermes/skills", "image-skills"),
    ("/hermes-profile-secrets", "profile-secrets"),
    ("/hermes-profile-env", "profile-env"),
    ("/honcho-client-secrets", "client-secrets"),
    ("/hermes-profiles", "configmap"),
    ("/opt/data", "data"),
]

PROFILE = "infra-expert"

# What must arrive base64-encoded (multi-line blob, decoded by bin/env.sh).
SECRET_FILES = {
    "KUBE_CONFIG": "apiVersion: v1\nkind: Config\nclusters: []\n",
}

# What must arrive verbatim (Hermes reads these itself).
VERBATIM_FILES = {
    "MATRIX_ACCESS_TOKEN": "syt_dummy_token_value_1",
    "MATRIX_HOMESERVER": "https://matrix.kalitsune.net",
    "MATRIX_ALLOWED_USERS": "@maple:kalitsune.net",
    "MATRIX_REQUIRE_MENTION": "true",
}


def find_script(built, container):
    with open(built) as fh:
        docs = [d for d in yaml.safe_load_all(fh) if d]
    cands = []
    for d in docs:
        spec = d.get("spec") or {}
        pod = ((spec.get("template") or {}).get("spec")) or {}
        cands += (pod.get("initContainers") or []) + (pod.get("containers") or [])
        values = spec.get("values") or {}
        cands += values.get("extraInitContainers") or []
        cands += values.get("extraContainers") or []
    for c in cands:
        if c.get("name") == container:
            cmd = c.get("command") or c.get("args") or []
            if not cmd:
                raise SystemExit(f"container '{container}' has no command")
            return cmd[-1]
    raise SystemExit(f"container '{container}' not found")


def build_sandbox(root):
    for _, sub in PATH_MAP:
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    cm = os.path.join(root, "configmap")
    open(os.path.join(cm, f"{PROFILE}--SOUL.md"), "w").write("soul\n")
    open(os.path.join(cm, f"{PROFILE}--config.yaml"), "w").write("model: {}\n")

    open(os.path.join(root, "client-secrets", "HONCHO_API_KEY"), "w").write("jwt.test.sig")

    sec = os.path.join(root, "profile-secrets", PROFILE)
    os.makedirs(sec, exist_ok=True)
    for k, v in SECRET_FILES.items():
        open(os.path.join(sec, k), "w").write(v)

    ver = os.path.join(root, "profile-env", PROFILE)
    os.makedirs(ver, exist_ok=True)
    for k, v in VERBATIM_FILES.items():
        # Kubernetes secret volumes have no trailing newline, but write one
        # anyway to prove the script strips it.
        open(os.path.join(ver, k), "w").write(v + "\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    script = find_script(sys.argv[1], "seed-profiles")
    root = tempfile.mkdtemp(prefix="verbenv-")
    failures = []
    try:
        build_sandbox(root)
        body = script
        for cpath, sub in PATH_MAP:
            body = body.replace(cpath, os.path.join(root, sub))
        sh = os.path.join(root, "script.sh")
        open(sh, "w").write(body)

        proc = subprocess.run(["sh", sh], capture_output=True, text=True)
        print(f"exit: {proc.returncode}")
        print("stdout:", proc.stdout.strip() or "(none)")
        if proc.stderr.strip():
            print("stderr:", proc.stderr.strip())
        if proc.returncode != 0:
            return proc.returncode or 1

        env_path = os.path.join(root, "data", "profiles", PROFILE, ".env")
        if not os.path.isfile(env_path):
            print("FAIL: .env not created")
            return 1

        env = {}
        for line in open(env_path):
            line = line.rstrip("\n")
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v

        print("\n--- assertions ---")

        # 1. Verbatim keys must be EXACTLY the plaintext value.
        for k, expected in VERBATIM_FILES.items():
            got = env.get(k)
            if got is None:
                failures.append(f"{k} missing from .env")
                continue
            if got != expected:
                failures.append(f"{k} = {got!r}, expected {expected!r}")
                continue
            # Guard the specific bug: value must not be base64 of itself.
            if got == base64.b64encode(expected.encode()).decode():
                failures.append(f"{k} was base64-encoded")
        ok_verbatim = not failures
        print(f"verbatim keys plaintext ({len(VERBATIM_FILES)}):",
              "PASS" if ok_verbatim else "FAIL")

        # The allowlist is the one that silently breaks auth.
        allow = env.get("MATRIX_ALLOWED_USERS", "")
        print(f"  MATRIX_ALLOWED_USERS = {allow!r}")
        if not allow.startswith("@"):
            failures.append("MATRIX_ALLOWED_USERS does not look like a Matrix ID")

        # 2. Secret blobs must still be base64 (bin/env.sh decodes them).
        for k, plain in SECRET_FILES.items():
            got = env.get(k)
            if got is None:
                failures.append(f"{k} missing from .env")
                continue
            try:
                decoded = base64.b64decode(got).decode()
            except Exception as e:
                failures.append(f"{k} is not valid base64: {e}")
                continue
            if decoded != plain:
                failures.append(f"{k} did not round-trip through base64")
        print("secret blobs still base64:",
              "PASS" if not any(k in f for f in failures for k in SECRET_FILES) else "FAIL")

        # 3. No value may contain a newline (would corrupt .env parsing).
        for k, v in env.items():
            if "\n" in v or "\r" in v:
                failures.append(f"{k} contains a newline")
        print("no embedded newlines:", "PASS" if not any("newline" in f for f in failures) else "FAIL")

        print()
        if failures:
            print("FAIL:")
            for f in failures:
                print("  -", f)
            return 1
        print("PASS")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
