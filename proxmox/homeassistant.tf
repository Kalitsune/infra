resource "proxmox_download_file" "haos_image" {
  node_name    = var.proxmox_node
  content_type = "iso"
  datastore_id = var.proxmox_iso_storage

  file_name = "haos.img"
  url       = "https://github.com/home-assistant/operating-system/releases/download/18.2/haos_ova-18.2.qcow2.xz"
}

resource "proxmox_virtual_environment_vm" "homeassistant" {
  node_name = var.proxmox_node
  vm_id     = 201
  name      = "home-assistant"

  on_boot = true
  started = false

  machine = "q35"
  bios    = "ovmf"

  cpu {
    cores = 2
    type  = "host"
  }

  memory {
    dedicated = 4096
  }

  efi_disk {
    datastore_id = var.proxmox_storage
    type         = "4m"
  }

  disk {
    interface    = "scsi0"
    datastore_id = var.proxmox_storage
    file_id      = proxmox_download_file.haos_image.id
    size         = 32
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  boot_order = ["scsi0"]

  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}
