# Terraform setup for the KK pilot

Start in a dedicated Terraform workspace under the operator account. Review `variables.tf` and copy `pilot.tfvars.example` to a private values file for the target tenancy. Resource creation is disabled by default.

Check the region, approved routing, CIDR ranges, availability domain, shape capacity and database secret reference. Run `terraform init`, `terraform validate` and `terraform plan` before enabling billable resources. Keep state files and provider credentials private.

Install the application and router separately using the [operations guide](../docs/OPERATIONS.md). The portable API binds to loopback and is reached over private SSH. If you add a listener or load balancer, you also need user authentication and appropriate access controls.
