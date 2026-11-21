output "deployment_enabled" {
  value = local.enabled
}
output "application_instance_ids" {
  value = { for k, v in oci_core_instance.app : k => v.id }
}
output "application_private_ips" {
  value = { for k, v in oci_core_instance.app : k => v.private_ip }
}
output "load_balancer_addresses" {
  value = try(oci_load_balancer_load_balancer.ingress[0].ip_addresses, [])
}
output "document_buckets" {
  value = { for k, v in oci_objectstorage_bucket.documents : k => v.name }
}
output "queue_id" {
  value = try(oci_queue_queue.jobs[0].id, null)
}
output "queue_messages_endpoint" {
  value = try(oci_queue_queue.jobs[0].messages_endpoint, null)
}
output "database_id" {
  value = try(oci_database_autonomous_database.results[0].id, null)
}
output "database_private_endpoint" {
  value = try(oci_database_autonomous_database.results[0].private_endpoint, null)
}
output "application_log_id" {
  value = try(oci_logging_log.application[0].id, null)
}
output "runtime_hold" {
  value = "Cloud-init stages directories only. Do not enable services or LB backends until shared adapters and auth have passed deployment-runbook gates."
}
