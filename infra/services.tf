resource "oci_core_instance" "app" {
  for_each            = local.nodes
  compartment_id      = var.compartment_id
  availability_domain = var.availability_domain
  fault_domain        = var.fault_domains[each.value]
  display_name        = "${var.name_prefix}-app-${each.value + 1}"
  shape               = var.compute_shape
  shape_config {
    ocpus         = 2
    memory_in_gbs = 16
  }
  source_details {
    source_type            = "image"
    source_id              = var.compute_image_id
    boot_volume_size_in_gbs = 50
  }
  create_vnic_details {
    subnet_id        = oci_core_subnet.tier["app"].id
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.tier["app"].id]
    hostname_label   = "app${each.value + 1}"
  }
  instance_options {
    are_legacy_imds_endpoints_disabled = true
  }
  agent_config {
    is_monitoring_disabled = false
    is_management_disabled = true
  }
  metadata = merge({
    user_data = base64encode(templatefile("${path.module}/templates/cloud-init.yaml.tftpl", {}))
  }, var.ssh_public_key == "" ? {} : { ssh_authorized_keys = var.ssh_public_key })
  freeform_tags = local.tags
  lifecycle {
    # Replacement requires explicit review; boot disks may hold pilot evidence.
    prevent_destroy = true
  }
}

resource "oci_objectstorage_bucket" "documents" {
  for_each       = local.enabled ? toset(["originals", "results"]) : toset([])
  compartment_id = var.compartment_id
  namespace      = var.object_storage_namespace
  name           = "${var.name_prefix}-${each.key}"
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  versioning     = "Enabled"
  freeform_tags  = local.tags
  # No PARs, lifecycle deletion or cross-region replication. Retention is a
  # customer decision; retained versions increase storage consumption.
  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_queue_queue" "jobs" {
  count                            = local.count
  compartment_id                   = var.compartment_id
  display_name                     = "${var.name_prefix}-jobs"
  retention_in_seconds             = 604800
  visibility_in_seconds            = 120
  timeout_in_seconds               = 20
  dead_letter_queue_delivery_count = 5
  freeform_tags                    = local.tags
  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_database_autonomous_database" "results" {
  count                                = local.count
  compartment_id                       = var.compartment_id
  display_name                         = "${var.name_prefix}-results"
  db_name                              = var.adb_db_name
  db_workload                          = "OLTP"
  source                               = "NONE"
  compute_model                        = "ECPU"
  compute_count                        = 4
  data_storage_size_in_gb               = 200
  license_model                        = "LICENSE_INCLUDED"
  is_free_tier                         = false
  is_dedicated                         = false
  is_auto_scaling_enabled              = false
  is_auto_scaling_for_storage_enabled  = false
  is_mtls_connection_required          = true
  subnet_id                            = oci_core_subnet.tier["db"].id
  nsg_ids                              = [oci_core_network_security_group.tier["db"].id]
  private_endpoint_label               = "kkkkadb"
  secret_id                            = var.adb_admin_secret_id
  secret_version_number                = var.adb_admin_secret_version
  freeform_tags                        = local.tags
  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_load_balancer_load_balancer" "ingress" {
  count                      = local.count
  compartment_id             = var.compartment_id
  display_name               = "${var.name_prefix}-https"
  shape                      = "flexible"
  is_private                 = !var.public_ingress_enabled
  subnet_ids                 = [oci_core_subnet.tier["lb"].id]
  network_security_group_ids = [oci_core_network_security_group.tier["lb"].id]
  shape_details {
    minimum_bandwidth_in_mbps = var.lb_minimum_bandwidth_in_mbps
    maximum_bandwidth_in_mbps = var.lb_maximum_bandwidth_in_mbps
  }
  freeform_tags = local.tags
}
resource "oci_load_balancer_backend_set" "app" {
  count            = local.count
  load_balancer_id = oci_load_balancer_load_balancer.ingress[0].id
  name             = "kk-app"
  policy           = "LEAST_CONNECTIONS"
  health_checker {
    protocol          = "HTTP"
    port              = 8080
    url_path          = "/api/health"
    return_code       = 200
    interval_ms       = 10000
    timeout_in_millis = 3000
    retries           = 3
  }
}
resource "oci_load_balancer_backend" "app" {
  for_each        = var.enable_backend_registration ? local.nodes : {}
  load_balancer_id = oci_load_balancer_load_balancer.ingress[0].id
  backendset_name  = oci_load_balancer_backend_set.app[0].name
  ip_address      = oci_core_instance.app[each.key].private_ip
  port            = 8080
  weight          = 1
}
resource "oci_load_balancer_listener" "https" {
  count                    = local.count
  load_balancer_id          = oci_load_balancer_load_balancer.ingress[0].id
  default_backend_set_name = oci_load_balancer_backend_set.app[0].name
  name                     = "https"
  port                     = 443
  protocol                 = "HTTP"
  ssl_configuration {
    certificate_ids  = [var.tls_certificate_id]
    protocols        = ["TLSv1.2", "TLSv1.3"]
    cipher_suite_name = "oci-tls-12-13-ssl-cipher-suite-v3"
  }
  connection_configuration {
    idle_timeout_in_seconds = 60
  }
}
