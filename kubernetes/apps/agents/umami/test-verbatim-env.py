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

# Pre-existing, unmanaged content of the DEFAULT profile's /opt/data/.env.
# The script must preserve every one of these while (re)writing MATRIX_ keys.
ROOT_ENV_EXISTING = (
    "# Hermes Agent Environment Configuration\n"
    "API_SERVER_KEY=preexisting-api-key\n"
    "TERMINAL_TIMEOUT=600\n"
    "# a comment\n"
    "WEB_TOOLS_DEBUG=1\n"
)

# Simulates a PREVIOUS run's MATRIX_ lines, to prove re-seeding is idempotent
# and does not duplicate or strand a stale value.
ROOT_ENV_STALE_MATRIX = "MATRIX_ACCESS_TOKEN=OLD_STALE_TOKEN\n"

DEFAULT_VERBATIM_FILES = {
    "MATRIX_ACCESS_TOKEN": "syt_dashboard_token_2",
    "MATRIX_HOMESERVER": "https://matrix.kalitsune.net",
    "MATRIX_ALLOWED_USERS": "@maple:kalitsune.net",
    "MATRIX_REQUIRE_MENTION": "true",
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-dummy-1",
    "GOOGLE_API_KEY": "AIza-dummy-1",
}

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
    # E2EE. Without this the adapter defaults to mode "off", receives
    # undecryptable m.room.encrypted events in encrypted rooms, and drops
    # them before the gateway ever sees an inbound message — the bot looks
    # like it is ignoring @mentions.
    "MATRIX_E2EE_MODE": "required",
    "MATRIX_DEVICE_ID": "BC91u0NjlW",
    # LLM provider credential. Must reach the profile .env or the agent
    # cannot talk to Anthropic at all under multiplexing:
    #   WARNING gateway.run: Primary provider auth failed: No Anthropic
    #   credentials found.
    "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-dummy-1",
    "GOOGLE_API_KEY": "AIza-dummy-1",
}

# HASS_TOKEN must NOT be seeded per-profile: it is required_env for the
# homeassistant platform, so both profiles would configure that adapter with
# one credential and trip the duplicate-credential guard.
FORBIDDEN_KEYS = ("HASS_TOKEN",)

# LLM provider credentials. These MUST reach every profile's .env — they are
# not platform adapters, so there is no duplicate-credential guard, and the
# agent cannot run without them.
PROVIDER_KEYS = ("CLAUDE_CODE_OAUTH_TOKEN",)


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

    # Default profile: its own credential dir, and a pre-existing root .env
    # holding unmanaged secrets plus a stale MATRIX_ line from an old run.
    dver = os.path.join(root, "profile-env", "default")
    os.makedirs(dver, exist_ok=True)
    for k, v in DEFAULT_VERBATIM_FILES.items():
        open(os.path.join(dver, k), "w").write(v + "\n")

    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    open(os.path.join(root, "data", ".env"), "w").write(
        ROOT_ENV_EXISTING + ROOT_ENV_STALE_MATRIX
    )


def check_manifest_projections(built):
    """Validate what the per-profile env volumes actually project.

    Returns (leaked, missing).

    leaked  = required_env platform creds (HASS_TOKEN) projected per-profile.
              Both profiles would configure that adapter with one credential
              and the gateway refuses to start all but the first.

    missing = provider creds NOT projected into a profile env dir. Under
              multiplexing the agent reads them through the profile secret
              scope, which never falls back to os.environ, so a key that is
              only in the process env yields:
                  Primary provider auth failed: No Anthropic credentials found
    """
    with open(built) as fh:
        docs = [d for d in yaml.safe_load_all(fh) if d]

    # Which volume backs each /hermes-profile-env/<profile> mount?
    mount_by_volume = {}
    values = {}
    for d in docs:
        if d.get("kind") != "HelmRelease":
            continue
        values = (d.get("spec") or {}).get("values") or {}
        for c in values.get("extraInitContainers") or []:
            for m in c.get("volumeMounts") or []:
                mp = str(m.get("mountPath", ""))
                if mp.startswith("/hermes-profile-env/"):
                    mount_by_volume[m["name"]] = mp.rsplit("/", 1)[-1]

    leaked, provided = [], {}
    for v in values.get("extraVolumes") or []:
        prof = mount_by_volume.get(v.get("name"))
        if not prof:
            continue
        provided.setdefault(prof, set())
        sources = (v.get("projected") or {}).get("sources") or []
        if not sources and v.get("secret"):
            sources = [{"secret": v["secret"]}]
        for s in sources:
            sec = s.get("secret") or {}
            name = sec.get("name") or sec.get("secretName") or ""
            items = sec.get("items")
            if items is None:
                if name == "hermes-secrets":
                    leaked.append(f"{v['name']}: whole hermes-secrets projected")
                # A whole-secret Matrix projection supplies the MATRIX_ keys.
                if "matrix" in name:
                    provided[prof] |= {"MATRIX_ACCESS_TOKEN"}
                continue
            for it in items:
                key = it.get("key")
                if key in FORBIDDEN_KEYS:
                    leaked.append(f"{v['name']}: {key}")
                provided[prof].add(key)

    missing = []
    for prof, keys in provided.items():
        for req in PROVIDER_KEYS:
            if req not in keys:
                missing.append(f"{prof}: {req}")
    if not provided:
        missing.append("no /hermes-profile-env mounts found at all")
    return leaked, missing


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

        # 4. DEFAULT profile: root .env must gain its own MATRIX_ keys while
        #    preserving pre-existing unmanaged content.
        root_env_path = os.path.join(root, "data", ".env")
        root_txt = open(root_env_path).read() if os.path.isfile(root_env_path) else ""
        root_env = {}
        for line in root_txt.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                root_env[k] = v

        pre_ok = True
        for line in ROOT_ENV_EXISTING.splitlines():
            if line not in root_txt:
                failures.append(f"root .env lost pre-existing line: {line!r}")
                pre_ok = False
        print("root .env preserved existing content:", "PASS" if pre_ok else "FAIL")

        dflt_ok = True
        for k, expected in DEFAULT_VERBATIM_FILES.items():
            got = root_env.get(k)
            if got != expected:
                failures.append(f"root .env {k} = {got!r}, expected {expected!r}")
                dflt_ok = False
        print("root .env has default-profile MATRIX keys:", "PASS" if dflt_ok else "FAIL")

        # The stale value from a previous run must be GONE, not duplicated.
        if "OLD_STALE_TOKEN" in root_txt:
            failures.append("root .env still contains the stale MATRIX token")
        dupes = [k for k in DEFAULT_VERBATIM_FILES
                 if root_txt.count(f"\n{k}=") + root_txt.startswith(f"{k}=") > 1]
        if dupes:
            failures.append(f"root .env has duplicate keys: {dupes}")
        print("stale MATRIX line replaced, no duplicates:",
              "PASS" if "OLD_STALE_TOKEN" not in root_txt and not dupes else "FAIL")

        # The two profiles MUST NOT share a token, or the gateway refuses to
        # start the duplicate adapter.
        if env.get("MATRIX_ACCESS_TOKEN") == root_env.get("MATRIX_ACCESS_TOKEN"):
            failures.append("both profiles share one MATRIX_ACCESS_TOKEN")
        print("profiles use distinct tokens:",
              "PASS" if env.get("MATRIX_ACCESS_TOKEN") != root_env.get("MATRIX_ACCESS_TOKEN") else "FAIL")

        # 5. MANIFEST-LEVEL checks. These read the manifest itself rather than
        #    the sandbox: the sandbox fixtures are written by this test, so
        #    they would pass no matter what the manifest actually mounts.
        leaked, missing = check_manifest_projections(sys.argv[1])
        if leaked:
            failures.append(f"platform credential projected per-profile: {leaked}")
        print("no required_env platform creds projected:",
              "PASS" if not leaked else f"FAIL {leaked}")

        if missing:
            failures.append(f"provider credential not projected: {missing}")
        print("provider creds projected into every profile:",
              "PASS" if not missing else f"FAIL {missing}")

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
