variable "proxmox_endpoint" {
  description = "Proxmox API endpoint (e.g. https://192.168.1.10:8006)"
  type        = string
}

variable "proxmox_api_token" {
  description = "Proxmox API token in the form user@realm!tokenid=secret"
  type        = string
  sensitive   = true
}

variable "proxmox_node" {
  description = "Proxmox node name to deploy the VM on"
  type        = string
}

variable "proxmox_storage" {
  description = "Proxmox storage pool for VM disks and ISO"
  type        = string
  default     = "local-lvm"
}

variable "proxmox_iso_storage" {
  description = "Proxmox storage pool for ISO images (must support snippets/iso content)"
  type        = string
  default     = "local"
}

variable "truenas_iso_url" {
  description = "Direct download URL for the TrueNAS Scale ISO"
  type        = string
  default     = "https://download.sys.truenas.net/TrueNAS-SCALE-ElectricEel/24.10.2.2/TrueNAS-SCALE-24.10.2.2.iso"
}

variable "vm_id" {
  description = "Proxmox VM ID"
  type        = number
  default     = 200
}

variable "vm_name" {
  description = "VM name"
  type        = string
  default     = "truenas-scale"
}

variable "vm_cpu_cores" {
  description = "Number of CPU cores"
  type        = number
  default     = 4
}

variable "vm_memory_mb" {
  description = "RAM in megabytes"
  type        = number
  default     = 16384
}

variable "vm_boot_disk_size" {
  description = "Boot disk size in GB"
  type        = number
  default     = 32
}

variable "vm_data_disks" {
  description = "Additional data disks for ZFS pools (sizes in GB)"
  type        = list(number)
  default     = [500, 500]
}

variable "vm_network_bridge" {
  description = "Proxmox network bridge for the VM"
  type        = string
  default     = "vmbr0"
}

variable "vm_vlan_id" {
  description = "VLAN tag for the VM network interface (null = untagged)"
  type        = number
  default     = null
}
