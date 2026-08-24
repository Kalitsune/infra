resource "proxmox_download_file" "haos_image" {
  node_name    = var.proxmox_node
  content_type = "iso"
  datastore_id = var.proxmox_iso_storage

  file_name = "haos.img"
  url       = "https://github.com/home-assistant/operating-system/releases/download/18.2/haos_ova-18.2.qcow2.xz"
}

# After first apply, import the boot disk manually on the Proxmox host:
#   qm importdisk 201 /var/lib/vz/template/iso/haos.img local-lvm --format raw
# Then in the Proxmox GUI: Hardware → unused disk → Edit → set as scsi0, enable boot order.
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
    datastore_id      = var.proxmox_storage
    type              = "4m"
    pre_enrolled_keys = false
  }

  network_device {
    bridge = "vmbr0"
    model  = "virtio"
  }

  scsi_hardware = "virtio-scsi-single"

  vga {
    type = "std"
  }
}
