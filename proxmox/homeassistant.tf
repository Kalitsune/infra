locals {
  proxmox_host    = regex("https?://([^:/]+)", var.proxmox_endpoint)[0]
  haos_vm_id      = 201
  haos_url        = "https://github.com/home-assistant/operating-system/releases/download/18.2/haos_ova-18.2.qcow2.xz"
  haos_cache_file = "/var/lib/vz/template/cache/haos_ova-18.2.qcow2.xz"
}

resource "proxmox_virtual_environment_vm" "homeassistant" {
  node_name = var.proxmox_node
  vm_id     = local.haos_vm_id
  name      = "home-assistant"

  on_boot = true
  started = false

  machine = "q35"
  bios    = "ovmf"

  agent {
    enabled = true
  }

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
  }

  efi_disk {
    datastore_id      = var.proxmox_storage
    type              = "4m"
    pre_enrolled_keys = false
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  serial_device {}

  scsi_hardware = "virtio-scsi-pci"

  vga {
    type = "std"
  }

  lifecycle {
    ignore_changes = [disk, boot_order]
  }
}

resource "terraform_data" "haos_disk_import" {
  triggers_replace = {
    vm_id = proxmox_virtual_environment_vm.homeassistant.id
    url   = local.haos_url
  }

  connection {
    type  = "ssh"
    host  = local.proxmox_host
    user  = "root"
    agent = true
  }

  provisioner "remote-exec" {
    inline = [
      "set -e",
      "if qm config ${local.haos_vm_id} | grep -q '^scsi0:'; then exit 0; fi",
      "mkdir -p /var/lib/vz/template/cache",
      "if [ ! -s '${local.haos_cache_file}' ] || ! xz -t '${local.haos_cache_file}' 2>/dev/null; then wget -q -O '${local.haos_cache_file}' '${local.haos_url}'; fi",
      "IMG=$(mktemp /tmp/haos.XXXXXX.qcow2)",
      "xz -dc '${local.haos_cache_file}' > \"$IMG\"",
      "qm importdisk ${local.haos_vm_id} \"$IMG\" ${var.proxmox_storage} --format raw",
      "rm -f \"$IMG\"",
      "DISK=$(qm config ${local.haos_vm_id} | grep '^unused0:' | awk '{print $2}')",
      "qm set ${local.haos_vm_id} --scsi0 \"$DISK,ssd=1,discard=on\"",
      "qm set ${local.haos_vm_id} --boot order=scsi0",
      "qm resize ${local.haos_vm_id} scsi0 32G",
    ]
  }
}
