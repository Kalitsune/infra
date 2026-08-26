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
  vm_id     = 300
  name      = "talos-cp-1"

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
    datastore_id = "local-lvm"
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
  vm_id     = 301
  name      = "talos-w1"

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
    datastore_id = "truenas-lvm"
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

resource "terraform_data" "talos_hookscripts" {
  triggers_replace = {
    cp_id     = proxmox_virtual_environment_vm.talos_controlplane.id
    worker_id = proxmox_virtual_environment_vm.talos_worker.id
  }

  connection {
    type  = "ssh"
    host  = local.proxmox_host
    user  = "root"
    agent = true
  }

  provisioner "remote-exec" {
    inline = [
      "qm set 300 --hookscript local:snippets/wait-truenas.sh",
      "qm set 301 --hookscript local:snippets/wait-truenas.sh",
    ]
  }
}
