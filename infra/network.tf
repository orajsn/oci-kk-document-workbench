data "oci_core_services" "regional" {
  count = local.count
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

resource "oci_core_vcn" "pilot" {
  count          = local.count
  compartment_id = var.compartment_id
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${var.name_prefix}-vcn"
  dns_label      = "kkkk"
  freeform_tags  = local.tags
}

# No default allow-all egress or SSH rule. Every subnet uses this empty list
# together with its explicit NSGs. The VCN-generated default list is unused.
resource "oci_core_security_list" "deny_default" {
  count          = local.count
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-no-default-rules"
}
resource "oci_core_network_security_group" "tier" {
  for_each       = local.enabled ? toset(["app", "db", "lb"]) : toset([])
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-${each.key}"
}
resource "oci_core_service_gateway" "regional" {
  count          = local.count
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-regional-services"
  services {
    service_id = one(data.oci_core_services.regional[0].services).id
  }
}
resource "oci_core_nat_gateway" "approved" {
  count          = local.enabled && var.enable_nat_egress ? 1 : 0
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-approved-egress"
}
resource "oci_core_internet_gateway" "ingress" {
  count          = local.enabled && var.public_ingress_enabled ? 1 : 0
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-ingress-only"
  enabled        = true
}
resource "oci_core_route_table" "private" {
  count          = local.count
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-private-routes"
  route_rules {
    destination       = one(data.oci_core_services.regional[0].services).cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.regional[0].id
  }
  dynamic "route_rules" {
    for_each = var.enable_nat_egress ? [1] : []
    content {
      destination       = "0.0.0.0/0"
      destination_type  = "CIDR_BLOCK"
      network_entity_id = oci_core_nat_gateway.approved[0].id
    }
  }
}
resource "oci_core_route_table" "ingress" {
  count          = local.count
  compartment_id = var.compartment_id
  vcn_id         = oci_core_vcn.pilot[0].id
  display_name   = "${var.name_prefix}-ingress-routes"
  dynamic "route_rules" {
    for_each = var.public_ingress_enabled ? [1] : []
    content {
      destination       = "0.0.0.0/0"
      destination_type  = "CIDR_BLOCK"
      network_entity_id = oci_core_internet_gateway.ingress[0].id
    }
  }
}
resource "oci_core_subnet" "tier" {
  for_each = local.enabled ? {
    app = var.app_subnet_cidr
    db  = var.db_subnet_cidr
    lb  = var.lb_subnet_cidr
  } : {}
  compartment_id             = var.compartment_id
  vcn_id                     = oci_core_vcn.pilot[0].id
  cidr_block                 = each.value
  display_name               = "${var.name_prefix}-${each.key}"
  dns_label                  = each.key
  prohibit_public_ip_on_vnic = each.key != "lb" || !var.public_ingress_enabled
  security_list_ids          = [oci_core_security_list.deny_default[0].id]
  route_table_id             = each.key == "lb" ? oci_core_route_table.ingress[0].id : oci_core_route_table.private[0].id
  freeform_tags              = local.tags
}

locals {
  # Stateful rules permit response traffic; no separate broad return rules.
  tcp_rules = local.enabled ? merge({
    lb_to_app = { tier = "lb", direction = "EGRESS", peer = oci_core_network_security_group.tier["app"].id, peer_type = "NETWORK_SECURITY_GROUP", port = 8080 }
    app_from_lb = { tier = "app", direction = "INGRESS", peer = oci_core_network_security_group.tier["lb"].id, peer_type = "NETWORK_SECURITY_GROUP", port = 8080 }
    app_to_db = { tier = "app", direction = "EGRESS", peer = oci_core_network_security_group.tier["db"].id, peer_type = "NETWORK_SECURITY_GROUP", port = 1522 }
    db_from_app = { tier = "db", direction = "INGRESS", peer = oci_core_network_security_group.tier["app"].id, peer_type = "NETWORK_SECURITY_GROUP", port = 1522 }
    app_to_services = { tier = "app", direction = "EGRESS", peer = one(data.oci_core_services.regional[0].services).cidr_block, peer_type = "SERVICE_CIDR_BLOCK", port = 443 }
    app_to_dns_tcp = { tier = "app", direction = "EGRESS", peer = "169.254.169.254/32", peer_type = "CIDR_BLOCK", port = 53 }
    app_to_imds = { tier = "app", direction = "EGRESS", peer = "169.254.169.254/32", peer_type = "CIDR_BLOCK", port = 80 }
  }, {
    for cidr in var.ingress_cidrs : "https_${cidr}" => { tier = "lb", direction = "INGRESS", peer = cidr, peer_type = "CIDR_BLOCK", port = 443 }
  }, {
    for cidr in var.management_cidrs : "ssh_${cidr}" => { tier = "app", direction = "INGRESS", peer = cidr, peer_type = "CIDR_BLOCK", port = 22 }
  }, {
    for cidr in var.egress_https_cidrs : "egress_${cidr}" => { tier = "app", direction = "EGRESS", peer = cidr, peer_type = "CIDR_BLOCK", port = 443 } if var.enable_nat_egress
  }) : {}
}
resource "oci_core_network_security_group_security_rule" "tcp" {
  for_each                  = local.tcp_rules
  network_security_group_id = oci_core_network_security_group.tier[each.value.tier].id
  direction                 = each.value.direction
  protocol                  = "6"
  stateless                 = false
  source                    = each.value.direction == "INGRESS" ? each.value.peer : null
  source_type               = each.value.direction == "INGRESS" ? each.value.peer_type : null
  destination               = each.value.direction == "EGRESS" ? each.value.peer : null
  destination_type          = each.value.direction == "EGRESS" ? each.value.peer_type : null
  tcp_options {
    destination_port_range {
      min = each.value.port
      max = each.value.port
    }
  }
}
resource "oci_core_network_security_group_security_rule" "local_services_udp" {
  for_each                  = local.enabled ? { dns = 53, ntp = 123 } : {}
  network_security_group_id = oci_core_network_security_group.tier["app"].id
  direction                 = "EGRESS"
  protocol                  = "17"
  destination               = "169.254.169.254/32"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
  udp_options {
    destination_port_range {
      min = each.value
      max = each.value
    }
  }
}
