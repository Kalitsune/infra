#!/usr/bin/env python3
"""Regression test: does seed-profiles preserve agent-authored skills?

Runs the REAL init script extracted from the built manifest against a sandbox
that mimics the live volume: bundled symlinks from a previous run, a stale
symlink from a category that no longer ships, and an agent-authored skill that
is a real directory present in no ConfigMap, no image and no git tree.

Usage: test-skill-preservation.py <built.yaml>
Exit 0 = the agent-authored skill survives.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

PATH_MAP = [
    ("/opt/hermes/skills", "image-skills"),
    ("/hermes-profile-secrets", "profile-secrets"),
    ("/honcho-client-secrets", "client-secrets"),
    ("/hermes-profiles", "configmap"),
    ("/opt/data", "data"),
]

# What the ConfigMap lists (mirrors profiles/infra-expert/skills in the repo)
BUNDLED = [
    "creative/architecture-diagram",
    "creative/excalidraw",
    "github/github-code-review",
    "github/github-pr-workflow",
    "autonomous-ai-agents/hermes-agent",
    "software-development/hermes-agent-skill-authoring",
    "software-development/plan",
    "software-development/requesting-code-review",
    "software-development/spike",
    "software-development/systematic-debugging",
]

# The agent-authored skill. Real directory, not a link, not in any ConfigMap.
AGENT_SKILL = "infrastructure/gitops-cluster-operations"
AGENT_FILES = [
    f"{AGENT_SKILL}/SKILL.md",
    f"{AGENT_SKILL}/references/verification-playbook.md",
    f"{AGENT_SKILL}/scripts/test-init-script.py",
]
# A bundled link from a category that no longer ships — must be cleaned up.
STALE_LINK = "media/old-removed-skill"


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

    # ConfigMap: SOUL, config, and the newline-separated skills list
    cm = os.path.join(root, "configmap")
    open(os.path.join(cm, "infra-expert--SOUL.md"), "w").write("soul\n")
    open(os.path.join(cm, "infra-expert--config.yaml"), "w").write("model: {}\n")
    open(os.path.join(cm, "infra-expert--skills"), "w").write("\n".join(BUNDLED) + "\n")

    open(os.path.join(root, "client-secrets", "HONCHO_API_KEY"), "w").write(
        "eyJhbGciOiJIUzI1NiJ9.TEST.sig"
    )
    sec = os.path.join(root, "profile-secrets", "infra-expert")
    os.makedirs(sec, exist_ok=True)
    open(os.path.join(sec, "KUBE_CONFIG"), "w").write("apiVersion: v1\nkind: Config\n")

    # Image-side skill sources that the symlinks point at
    img = os.path.join(root, "image-skills")
    for s in BUNDLED + [STALE_LINK]:
        d = os.path.join(img, s)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "SKILL.md"), "w").write(f"bundled {s}\n")

    # Prior state of the live volume
    sk = os.path.join(root, "data", "profiles", "infra-expert", "skills")
    for s in BUNDLED[:4] + [STALE_LINK]:          # links a previous run made
        os.makedirs(os.path.join(sk, os.path.dirname(s)), exist_ok=True)
        os.symlink(os.path.join(img, s), os.path.join(sk, s))
    for rel in AGENT_FILES:                        # the irreplaceable part
        p = os.path.join(sk, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(f"agent-authored {rel}\n")
    # curator bookkeeping lives at the skills root
    open(os.path.join(sk, ".curator_ledger.jsonl"), "w").write('{"e":1}\n')
    return sk


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    script = find_script(sys.argv[1], "seed-profiles")
    root = tempfile.mkdtemp(prefix="seedtest-")
    failures = []
    try:
        sk = build_sandbox(root)
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

        print("\n--- assertions ---")

        # 1. THE REGRESSION: agent-authored skill must survive intact
        for rel in AGENT_FILES:
            p = os.path.join(sk, rel)
            if not os.path.isfile(p):
                failures.append(f"DESTROYED agent-authored file: {rel}")
            elif not open(p).read().startswith("agent-authored"):
                failures.append(f"CLOBBERED agent-authored file: {rel}")
        print(
            f"agent-authored skill ({len(AGENT_FILES)} files): "
            + ("SURVIVED" if not failures else "LOST")
        )

        # 2. curator ledger at the skills root must survive
        led = os.path.join(sk, ".curator_ledger.jsonl")
        ok = os.path.isfile(led)
        print("curator ledger:", "survived" if ok else "DESTROYED")
        if not ok:
            failures.append("curator ledger destroyed")

        # 3. every bundled skill must be a working symlink
        bad = []
        for s in BUNDLED:
            p = os.path.join(sk, s)
            if not os.path.islink(p):
                bad.append(f"{s} (not a link)")
            elif not os.path.isfile(os.path.join(os.path.realpath(p), "SKILL.md")):
                bad.append(f"{s} (dangling)")
        print(f"bundled skills relinked: {len(BUNDLED) - len(bad)}/{len(BUNDLED)}")
        if bad:
            failures.append("bundled link problems: " + ", ".join(bad))

        # 4. stale link from a removed category must be gone
        stale = os.path.join(sk, STALE_LINK)
        gone = not os.path.lexists(stale)
        print("stale link cleaned:", "yes" if gone else "NO - still present")
        if not gone:
            failures.append(f"stale link {STALE_LINK} not cleaned")

        # 5. no real directory was left empty / no symlink target followed
        if os.path.isdir(os.path.join(root, "image-skills", BUNDLED[0])):
            src = os.path.join(root, "image-skills", BUNDLED[0], "SKILL.md")
            if not os.path.isfile(src):
                failures.append("symlink target in image was deleted (followed link!)")
        print("image-side sources intact:", "yes" if not any(
            "followed link" in f for f in failures) else "NO")

        # 6. honcho.json still valid (heredoc not mangled)
        hj = os.path.join(root, "data", "honcho.json")
        if not os.path.isfile(hj):
            failures.append("honcho.json not created")
            print("honcho.json: MISSING")
        else:
            try:
                parsed = json.loads(open(hj).read())
                key = parsed.get("apiKey", "")
                subst = bool(key) and not key.startswith("$")
                print(f"honcho.json: valid JSON, apiKey substituted={subst}, "
                      f"mode={oct(os.stat(hj).st_mode & 0o777)}")
                if not subst:
                    failures.append("honcho.json apiKey unsubstituted")
            except json.JSONDecodeError as e:
                failures.append(f"honcho.json invalid: {e}")
                print("honcho.json: INVALID JSON")

        # 7. .env seeded
        env = os.path.join(root, "data", "profiles", "infra-expert", ".env")
        has = os.path.isfile(env) and "KUBE_CONFIG=" in open(env).read()
        print(".env seeded:", "yes" if has else "NO")
        if not has:
            failures.append(".env not seeded")

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
