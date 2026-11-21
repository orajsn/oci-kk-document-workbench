terraform {
  required_version = ">= 1.9.0, < 2.0.0"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "9.13.0"
    }
  }
}

# Authentication is supplied by the approved execution environment. No API keys,
# passwords, certificates, wallets or secret contents are read by this source.
provider "oci" {
  region = var.region
}

locals {
  enabled = var.enable_deployment
  count   = local.enabled ? 1 : 0
  nodes   = local.enabled ? { for i in range(2) : tostring(i) => i } : {}
  tags = {
    Project     = "KK-KK-pilot"
    Environment = "pilot"
    ManagedBy   = "Terraform"
  }
}
