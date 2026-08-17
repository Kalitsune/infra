terraform {
  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "~> 0.78"
    }
  }
  required_version = ">= 1.8"
}

provider "proxmox" {
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = true # set to false if your Proxmox has a trusted TLS cert
}

resource "proxmox_virtual_environment_download_file" "truenas_iso" {
  node_name    = var.proxmox_node
  content_type = "iso"
  datastore_id = var.proxmox_iso_storage

  file_name = "truenas-scale.iso"
  url       = var.truenas_iso_url
}

resource "proxmox_virtual_environment_vm" "truenas" {
  node_name = var.proxmox_node
  vm_id     = var.vm_id
  name      = var.vm_name

  on_boot  = true
  started  = false # start manually after post-install config

  machine = "q35"
  bios    = "ovmf"

  cpu {
    cores = var.vm_cpu_cores
    type  = "host"
  }

  memory {
    dedicated = var.vm_memory_mb
  }

  efi_disk {
    datastore_id = var.proxmox_storage
    type         = "4m"
  }

  # Boot disk
  disk {
    interface    = "scsi0"
    datastore_id = var.proxmox_storage
    size         = var.vm_boot_disk_size
    file_format  = "raw"
    ssd          = true
    discard      = "on"
    iothread     = true
  }

  # Data disks for ZFS pools
  dynamic "disk" {
    for_each = var.vm_data_disks
    content {
      interface    = "scsi${disk.key + 1}"
      datastore_id = var.proxmox_storage
      size         = disk.value
      file_format  = "raw"
      ssd          = true
      discard      = "on"
      iothread     = true
    }
  }

  cdrom {
    enabled   = true
    file_id   = proxmox_virtual_environment_download_file.truenas_iso.id
    interface = "ide0"
  }

  network_device {
    bridge  = var.vm_network_bridge
    model   = "virtio"
    vlan_id = var.vm_vlan_id
  }

  boot_order = ["scsi0", "ide0"]

  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}
