# Infrastructure template

Use an isolated Terraform workspace under the operator account. Inspect `variables.tf`, copy `pilot.tfvars.example` to a private values file, and check region, routing, CIDR allocation, availability domain, shape capacity and database secret reference for the target tenancy. Run `terraform init`, `terraform validate` and `terraform plan` there. The template defaults to disabled resource creation. Review the plan before enabling billable resources. This repository does not carry Terraform state or provider credentials.

Install the server and router separately as described in [the operations guide](../docs/OPERATIONS.md). Bind the application to loopback and reach it through private SSH access. A listener or load balancer template alone does not implement end-user authentication.
