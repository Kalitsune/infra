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
  description = "Proxmox storage pool for VM disks"
  type        = string
  default     = "local-lvm"
}

variable "proxmox_iso_storage" {
  description = "Proxmox storage pool for ISO images (must support iso content)"
  type        = string
  default     = "local"
}
