# talos/

Machine config **patches** only. The rendered `controlplane.yaml` / `worker.yaml`
contain the cluster PKI private keys and are never committed — they were, once,
and every key in them had to be rotated. See `.gitignore`.

Cluster: `talos-proxmox-cluster`, endpoint `https://192.168.1.170:6443`,
control plane `192.168.1.170`, worker `192.168.1.171`.

## Render

`secrets.yaml` holds the CAs and tokens. It lives only on the operator's machine
(and a password manager). If you do not have it, you cannot render a config that
this cluster will accept — generate a new one only when rebuilding from scratch.

```sh
cd talos
# first time only, or after a CA rotation:
talosctl gen secrets -o secrets.yaml

talosctl gen config talos-proxmox-cluster https://192.168.1.170:6443 \
  --with-secrets secrets.yaml \
  --config-patch @patches/common.yaml \
  --config-patch-control-plane @patches/controlplane.yaml \
  --output-dir .
```

`--output-dir .` writes `controlplane.yaml`, `worker.yaml` and `talosconfig`;
all three are gitignored.

## Apply

```sh
export TALOSCONFIG=talos/talosconfig
talosctl apply-config -n 192.168.1.170 -f talos/controlplane.yaml
talosctl apply-config -n 192.168.1.171 -f talos/worker.yaml
```

A human runs this. The control plane is a single VM — a bad machine config takes
the cluster with it.

## What is not in the patches

Everything else is `talosctl gen config` default for the pinned Talos version
(`ghcr.io/siderolabs/installer:v1.13.4`, Kubernetes v1.36.1): kubelet image and
seccomp defaults, KubePrism, host DNS, disk quota support, the PodSecurity
admission config and the Metadata audit policy. Bump the version by changing
`patches/common.yaml` and re-rendering, not by editing an output file.
