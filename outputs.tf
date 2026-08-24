output "vm_id" {
  description = "Proxmox VM ID of the TrueNAS Scale instance"
  value       = proxmox_virtual_environment_vm.truenas.vm_id
}

output "vm_name" {
  description = "VM name"
  value       = proxmox_virtual_environment_vm.truenas.name
}
