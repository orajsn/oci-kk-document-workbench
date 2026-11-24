# Run the private OCI deployment

The API accepts images in Batam, Object Storage keeps the originals, and Oracle Autonomous Database stores household and member records. A worker retrieves jobs from OCI Queue. The VM signs OCI requests with its instance principal. The routing profile forwards inference requests to the approved model endpoint outside Indonesia.

Use this runbook for a private operator installation. The public source package does not provision the hosted pilot's participant login or HTTPS gateway. To run the two-card exercise without cloud resources, start with [First batch](DAY-ONE.md).

## Prepare the VM and database

Provision a private Linux VM with Python 3.11 and cgroup v2, private network access, an Autonomous Database, a versioned bucket, OCI Queue, an approved routing profile for the vision model, and an operator access path. If you use the [Terraform template](../infra/README.md), run `terraform init`, `terraform validate` and `terraform plan` in the receiving tenancy. Resource creation defaults to disabled. Review the plan before enabling billable resources.

Install the application at `/opt/kk-pilot/current` with its virtual environment at `/opt/kk-pilot/venv`. Create the `kkpilot` service account.

The DBA prepares `KK_OWNER`, `KK_RUNTIME` and `KK_IMPORTER`. Run `sql/00_account_grants.sql` as administrator. As the schema owner, run the following files from `sql/` in this order.

1. `01_schema.sql`, then `02_runtime_grants.sql`
2. `04_cloud_schema.sql`, then `05_cloud_grants.sql`
3. `06_nik_review.sql`, then `07_nik_router.sql`

`03_operational_queries.sql` contains operator queries. Oracle DDL commits implicitly. If installation stops partway through, inspect the objects already created before resuming.

## Configure the OCI connections

Copy `kkpilot/cloud/config.example.json` to `/etc/kk-pilot/cloud.json` and `deploy/runtime.env.example` to `/etc/kk-pilot/runtime.env` on the VM.

- Set the compartment, private database host and service, queue OCID and messages endpoint, namespace, bucket, routing profile, pilot scope and approved image hashes.
- Match the independent environment pins `KK_OCI_COMPARTMENT_OCID` and `KK_OCI_ROUTING_PROFILE_OCID` to the approved resources in the JSON configuration.
- Keep the database password, mTLS wallet and wallet password in your team's secret store. Restrict access to the systemd environment file. Configure the operator identity recorded with each decision.
- Set the data-processing and spending approvals, dataset authorization reference and acknowledgement required by `kkpilot/cloud/config.py`. Confirm that the data owner has approved the model and actual processing destination for those images.

Run these commands with the same environment as the service.

```sh
python -m kkpilot.cloud plan --config /etc/kk-pilot/cloud.json
python -m kkpilot.cloud preflight --config /etc/kk-pilot/cloud.json
```

Inspect the reported model, destination, lease, visibility timeout and call budget. Use `preflight --execute` from the configured private VM to probe cloud resources. Restrict IAM policies to the approved bucket, queue, model/profile and compartment. The router uses the same inference endpoint.

## Install OCR and verify the image coordinates

Install `kkpilot/cloud/requirements.operator.txt` in the application environment. OCR uses a separate environment under `/opt/kk-pilot/ocr-runtime-v1/venv` and the packages in `deploy/requirements-ocr.txt`. Obtain **PP-OCRv5_mobile_det_infer** and **PP-OCRv5_server_rec_infer** from PaddleOCR and place them under `/opt/kk-pilot/ocr-runtime-v1/models`.

Verify licensing, download checksums and native dependencies for the VM. Pin the installed model manifest. `local_paddle_ocr.verify_models` checks file hashes and `check_resource_limits` checks CPU, RAM, swap and PID limits. Run a smoke test as the service account with the intended cgroup limits. The OCR worker requires kernel user and network namespaces.

The initial approved configuration uses the full-image Gemini path. For hybrid OCR, prepare the private source-region manifest, enable `focused_refinement_enabled` and `local_ocr.enabled`, and set the budget required by the validator. Field corroboration (`field_corroboration_enabled`), row alignment (`nik_row_alignment_enabled`) and three-way reading each have additional prerequisites. Three-way reading requires Document Understanding authorization and its additional call budget. [Verify crop geometry on approved calibration images](GEOMETRY.md).

## Start the services

Create `/var/lib/kk-pilot` and its `tmp`, `diagnostics` and `bulk-nik-router` directories with service ownership and private permissions. The router reads diagnostic records that may contain personal information. Agree on retention with the data owner.

Copy `nik_router/` to `/opt/kk-pilot/nik-router/current`. The initial crop profile is an empty JSON object. The locator can use OCR to identify a member row. If the row cannot be located reliably, the NIK needs review. The portable intake still requires an approved source-image hash.

Install the units from `deploy/` and check their `ExecStart` paths. Activate the correct database scope before starting the router timer.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kk-pilot-api.service kk-pilot-dispatcher.service kk-pilot-worker.service
# In SQLcl as KK_OWNER, use the scope from cloud.json:
# @deploy/activate-scope.sql my-approved-pilot
sudo systemctl enable --now kk-pilot-nik-router.timer
```

Access the application through a private tunnel to loopback port 8765. The operator interface uses scope controls, a CSRF token and a configured operator identity. For one upload, follow the receipt, object version, worker attempt, Oracle commit and `/api/jobs/<id>/nik-routing` status. The router selects `MODEL` records in the configured scope starting at the database activation time. [Verify a stored decision](HANDOVER.md#verify-a-batch-in-oci).

## Monitor and roll back

Check `systemctl status kk-pilot-{api,dispatcher,worker}.service`, `systemctl status kk-pilot-nik-router.timer` and each service's journal. Monitor the oldest queued job, upload-to-commit time, routing completion, incorrect NIKs that passed, operator workload, and calls and tokens per job. Review events carry a request ID and expected revision. The original AI extraction remains available.

Tag the old and new application and router builds. Keep configuration, hashes and backups privately. To roll back the router, stop its timer, allow active work to finish, restore the previously tested build and restart the changed services. Preserve Oracle records, source images and receipts. Reopen an earlier result and decision to verify recovery.

| Component | Required access |
| --- | --- |
| API and worker | Queue messages, versioned private objects, routing-profile reads and approved inference calls via instance principal. |
| `KK_RUNTIME` | Read scoped records and insert extractions, cases and review events according to the SQL grants. |
| DBA | Manage schemas, grants, private connectivity, credentials and wallet and activate the post-save scope after configuration acceptance. |
| NIK router | Read the private API and diagnostic evidence, make one reread attempt for a flagged row when its crop is reliable, and record cases and receipts idempotently. |

Review policies with the tenancy administrator. Grant Document Understanding access only when that path is enabled. Use fictional images in the public browser demo.
