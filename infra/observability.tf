resource "oci_logging_log_group" "pilot" {
  count          = local.count
  compartment_id = var.compartment_id
  display_name   = "${var.name_prefix}-operations"
  description    = "Operational logs; do not log KK images, prompts, payloads or identifiers."
  freeform_tags  = local.tags
}
resource "oci_logging_log" "lb_error" {
  count              = local.count
  display_name       = "load-balancer-errors"
  log_group_id       = oci_logging_log_group.pilot[0].id
  log_type           = "SERVICE"
  is_enabled         = true
  retention_duration = 30
  configuration {
    compartment_id = var.compartment_id
    source {
      source_type = "OCISERVICE"
      service     = "loadbalancer"
      resource    = oci_load_balancer_load_balancer.ingress[0].id
      category    = "Error"
    }
  }
}
resource "oci_logging_log" "application" {
  count              = local.count
  display_name       = "application-redacted"
  log_group_id       = oci_logging_log_group.pilot[0].id
  log_type           = "CUSTOM"
  is_enabled         = true
  retention_duration = 30
  # Destination only: uploader/agent config is an explicit later integration.
}
resource "oci_monitoring_alarm" "app_cpu" {
  for_each              = local.nodes
  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  display_name          = "${var.name_prefix}-app-${each.value + 1}-cpu"
  namespace             = "oci_computeagent"
  query                 = "CpuUtilization[1m]{resourceId = \"${oci_core_instance.app[each.key].id}\"}.mean() > 80"
  severity              = "WARNING"
  is_enabled            = var.alarms_enabled
  destinations          = [var.notification_topic_id]
  pending_duration      = "PT5M"
  body                  = "Check app CPU, backlog and worker concurrency. Do not increase model concurrency without quota/cost review."
}
resource "oci_monitoring_alarm" "queue_lag" {
  count                 = local.count
  compartment_id        = var.compartment_id
  metric_compartment_id = var.compartment_id
  display_name          = "${var.name_prefix}-queue-lag"
  namespace             = "oci_queue"
  query                 = "ConsumerLag[1m]{resourceId = \"${oci_queue_queue.jobs[0].id}\"}.max() > 1"
  severity              = "CRITICAL"
  is_enabled            = var.alarms_enabled
  destinations          = [var.notification_topic_id]
  pending_duration      = "PT2M"
  body                  = "Oldest queue message exceeds one minute. Check worker health, inference throttling and DB outbox reconciliation. This is not the 10-second latency SLO alarm."
}
