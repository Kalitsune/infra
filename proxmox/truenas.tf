resource "proxmox_download_file" "truenas_iso" {
  node_name    = var.proxmox_node
  content_type = "iso"
  datastore_id = var.proxmox_iso_storage

  file_name = "truenas-scale.iso"
  url       = "https://download.sys.truenas.net/TrueNAS-SCALE-ElectricEel/24.10.2.2/TrueNAS-SCALE-24.10.2.2.iso"
}

resource "proxmox_virtual_environment_vm" "truenas" {
  node_name = var.proxmox_node
  vm_id     = 200
  name      = "truenas-scale"

  on_boot = true
  started = false

  machine = "q35"
  bios    = "ovmf"

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 16384
  }

  efi_disk {
    datastore_id = var.proxmox_storage
    type         = "4m"
  }

  disk {
    interface    = "scsi0"
    datastore_id = var.proxmox_storage
    size         = 32
    file_format  = "raw"
    ssd          = true
    discard      = "on"
    iothread     = true
  }

  cdrom {
    file_id   = proxmox_download_file.truenas_iso.id
    interface = "ide0"
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  boot_order = ["scsi0", "ide0"]

  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}
