variable "enable_deployment" {
  description = "Explicitly create the proposed paid infrastructure; false creates no resources."
  type        = bool
  default     = false
}
variable "billable_deployment_ack" {
  type    = string
  default = ""
  validation {
    condition     = !var.enable_deployment || var.billable_deployment_ack == "APPROVED_KK_PILOT_BILLABLE_DEPLOYMENT"
    error_message = "Deployment needs the approved billable deployment acknowledgement."
  }
}
variable "region" {
  type    = string
  default = "ap-batam-1"
  validation {
    condition     = var.region == "ap-batam-1"
    error_message = "This baseline keeps application/data resources in Batam. Review a region change as a separate design change."
  }
}
variable "compartment_id" {
  type    = string
  default = ""
  validation {
    condition     = !var.enable_deployment || startswith(var.compartment_id, "ocid1.compartment.")
    error_message = "Supply the specifically approved pilot compartment OCID."
  }
}
variable "name_prefix" {
  type    = string
  default = "kk-pilot"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,31}$", var.name_prefix))
    error_message = "Use 3-32 lowercase letters, digits or hyphens, beginning with a letter."
  }
}
variable "object_storage_namespace" {
  type    = string
  default = ""
  validation {
    condition     = !var.enable_deployment || length(var.object_storage_namespace) > 0
    error_message = "Supply the tenancy Object Storage namespace; it is not the tenancy name."
  }
}
variable "availability_domain" {
  description = "Exact tenancy-specific AD name verified in Batam."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || length(var.availability_domain) > 0
    error_message = "Verify and supply the Batam availability domain name."
  }
}
variable "fault_domains" {
  description = "Two distinct verified fault domains in the selected AD."
  type        = list(string)
  default     = ["FAULT-DOMAIN-1", "FAULT-DOMAIN-2"]
  validation {
    condition     = length(var.fault_domains) == 2 && length(distinct(var.fault_domains)) == 2
    error_message = "Specify exactly two distinct fault domains."
  }
}
variable "compute_shape" {
  description = "Verified compatible x86 Flex shape; no capacity is assumed by this template."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || can(regex("^VM\\..*\\.Flex$", var.compute_shape))
    error_message = "Supply an approved VM Flex shape with confirmed Batam capacity for two 2-OCPU/16-GB instances."
  }
}
variable "compute_image_id" {
  description = "Approved Batam Linux x86 image containing cloud-init, systemd and Python 3.11+ (or a later authorized install)."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || startswith(var.compute_image_id, "ocid1.image.")
    error_message = "Supply the approved region-specific image OCID."
  }
}
variable "capacity_and_image_verified" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_deployment || var.capacity_and_image_verified
    error_message = "Verify image, shape, AD, both fault domains, limits and launch capacity first."
  }
}
variable "ssh_public_key" {
  description = "Optional approved public key. No SSH ingress is created unless management_cidrs is supplied."
  type        = string
  default     = ""
}
variable "vcn_cidr" {
  type    = string
  default = "10.76.0.0/16"
}
variable "app_subnet_cidr" {
  type    = string
  default = "10.76.10.0/24"
}
variable "db_subnet_cidr" {
  type    = string
  default = "10.76.20.0/24"
}
variable "lb_subnet_cidr" {
  type    = string
  default = "10.76.30.0/24"
}
variable "ingress_cidrs" {
  description = "Approved source CIDRs for HTTPS, including private pilot testers. Empty means no ingress."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for c in var.ingress_cidrs : try(cidrnetmask(c) != "0.0.0.0", false)])
    error_message = "Use explicit IPv4 source CIDRs; unrestricted 0.0.0.0/0 is prohibited."
  }
}
variable "management_cidrs" {
  description = "Optional private VPN/Bastion sources for SSH. This template does not create a VPN, DRG or Bastion."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for c in var.management_cidrs : try(cidrnetmask(c) != "0.0.0.0", false)])
    error_message = "Use explicit management CIDRs; unrestricted SSH is prohibited."
  }
}
variable "public_ingress_enabled" {
  type    = bool
  default = false
}
variable "public_ingress_ack" {
  type    = string
  default = ""
  validation {
    condition     = !var.public_ingress_enabled || (var.public_ingress_ack == "APPROVED_RESTRICTED_PUBLIC_HTTPS" && length(var.ingress_cidrs) > 0)
    error_message = "Public ingress requires explicit acknowledgement and restricted source CIDRs."
  }
}
variable "tls_certificate_id" {
  description = "Existing approved OCI Certificates certificate OCID in Batam; certificate private key is never read here."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || startswith(var.tls_certificate_id, "ocid1.certificate.")
    error_message = "Supply the existing TLS certificate OCID, with service permissions and DNS name confirmed."
  }
}
variable "lb_minimum_bandwidth_in_mbps" {
  description = "Approved LB minimum. 25 Mbps is the initial test allocation, not measured sizing."
  type        = number
  default     = 25
  validation {
    condition     = var.lb_minimum_bandwidth_in_mbps >= 10 && floor(var.lb_minimum_bandwidth_in_mbps) == var.lb_minimum_bandwidth_in_mbps
    error_message = "Use an integer LB minimum of at least 10 Mbps and review its commercial effect."
  }
}
variable "lb_maximum_bandwidth_in_mbps" {
  description = "Approved LB maximum; defaults to fixed 25 Mbps. Raising it can increase cost."
  type        = number
  default     = 25
  validation {
    condition     = var.lb_maximum_bandwidth_in_mbps >= var.lb_minimum_bandwidth_in_mbps && var.lb_maximum_bandwidth_in_mbps <= 1000 && floor(var.lb_maximum_bandwidth_in_mbps) == var.lb_maximum_bandwidth_in_mbps
    error_message = "Use an integer maximum >= minimum and <= this pilot template's 1000-Mbps review ceiling."
  }
}
variable "enable_backend_registration" {
  description = "Keep false for the SQLite prototype. Enable only after shared adapters and authentication are integrated."
  type        = bool
  default     = false
}
variable "shared_runtime_ack" {
  type    = string
  default = ""
  validation {
    condition     = !var.enable_backend_registration || var.shared_runtime_ack == "SHARED_STORAGE_DATABASE_QUEUE_AND_AUTH_VERIFIED"
    error_message = "Register both VMs only after shared durable adapters, multi-node correctness and authentication have passed."
  }
}
variable "enable_nat_egress" {
  description = "Optional approved HTTPS egress for endpoints not reachable through the regional service gateway."
  type        = bool
  default     = false
}
variable "egress_ack" {
  type    = string
  default = ""
  validation {
    condition     = !var.enable_nat_egress || (var.egress_ack == "APPROVED_ENDPOINTS_AND_PROCESSING_REGIONS" && length(var.egress_https_cidrs) > 0)
    error_message = "NAT requires approved endpoint/processing-region acknowledgement and destination CIDRs."
  }
}
variable "egress_https_cidrs" {
  description = "Approved endpoint/proxy IP ranges only. NSGs cannot filter by DNS name; operate an egress proxy if needed."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for c in var.egress_https_cidrs : try(cidrnetmask(c) != "0.0.0.0", false)])
    error_message = "Use explicit IPv4 destination CIDRs, never unrestricted HTTPS egress."
  }
}
variable "adb_admin_secret_id" {
  description = "Existing Vault secret for ADMIN password. The deploying identity needs scoped secret-bundle read permission."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || startswith(var.adb_admin_secret_id, "ocid1.vaultsecret.")
    error_message = "Supply the existing approved ADMIN secret OCID. No plaintext password input is supported."
  }
}
variable "adb_admin_secret_version" {
  type    = number
  default = 1
  validation {
    condition     = var.adb_admin_secret_version >= 1 && floor(var.adb_admin_secret_version) == var.adb_admin_secret_version
    error_message = "Pin a positive integer Vault secret version."
  }
}
variable "adb_db_name" {
  type    = string
  default = "KKPILOT"
  validation {
    condition     = can(regex("^[A-Za-z][A-Za-z0-9]{0,13}$", var.adb_db_name))
    error_message = "Use an Autonomous database name of 1-14 alphanumeric characters beginning with a letter."
  }
}
variable "notification_topic_id" {
  description = "Existing approved ONS topic with an operator-confirmed subscription; this source sends no subscriptions or emails."
  type        = string
  default     = ""
  validation {
    condition     = !var.enable_deployment || startswith(var.notification_topic_id, "ocid1.onstopic.")
    error_message = "Supply the operational notification topic and confirm its subscriptions before deployment."
  }
}
variable "alarms_enabled" {
  description = "Enable operational alarms on deployment. Alerts are not a budget cap or a shutdown control."
  type        = bool
  default     = true
}
