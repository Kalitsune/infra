locals {
  talos_version = "v1.9.5"
}

resource "proxmox_download_file" "talos_iso" {
  node_name    = var.proxmox_node
  content_type = "iso"
  datastore_id = var.proxmox_iso_storage

  file_name = "talos-${local.talos_version}-amd64.iso"
  url       = "https://github.com/siderolabs/talos/releases/download/${local.talos_version}/metal-amd64.iso"
}

resource "proxmox_virtual_environment_vm" "talos_controlplane" {
  node_name = var.proxmox_node
  vm_id     = 210
  name      = "talos-controlplane"

  on_boot = true
  started = false

  machine = "q35"
  bios    = "seabios"

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
  }

  disk {
    interface    = "scsi0"
    datastore_id = var.proxmox_storage
    size         = 20
    file_format  = "raw"
    ssd          = true
    discard      = "on"
    iothread     = true
  }

  cdrom {
    file_id   = proxmox_download_file.talos_iso.id
    interface = "ide0"
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  boot_order    = ["ide0", "scsi0"]
  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}

resource "proxmox_virtual_environment_vm" "talos_worker" {
  node_name = var.proxmox_node
  vm_id     = 211
  name      = "talos-worker"

  on_boot = true
  started = false

  machine = "q35"
  bios    = "seabios"

  cpu {
    cores = 8
    type  = "host"
  }

  memory {
    dedicated = 16384
  }

  disk {
    interface    = "scsi0"
    datastore_id = var.proxmox_storage
    size         = 100
    file_format  = "raw"
    ssd          = true
    discard      = "on"
    iothread     = true
  }

  cdrom {
    file_id   = proxmox_download_file.talos_iso.id
    interface = "ide0"
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  boot_order    = ["ide0", "scsi0"]
  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}
