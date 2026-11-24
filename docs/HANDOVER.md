# Verify a Workbench deployment

A handover is complete when an operator can open a saved household, find the right member, record a decision against the print and retrieve both the original reading and that decision. The [local exercise](DAY-ONE.md) covers the first steps with two fictional images. Use this guide to verify an installation in the receiving team's OCI tenancy.

## Before the test

| Owner | What to prepare |
| --- | --- |
| Process owner | Required fields for each form layout, accepted handling of legacy numbers and unreadable print, and two images approved for processing. Include one known ambiguous NIK to exercise review. |
| OCI platform team | Approved region and model routing profile, private access, dynamic group and least-privilege policies. Confirm the model-processing destination with the data owner. |
| Application team | Application and router versions, prompt and schema versions, OCR model hashes, private crop profiles, test results and the previous working build. |
| DBA | Extraction and review tables, runtime grants, wallet, backup and restore procedure, and read access for acceptance queries. |
| Operations | Health checks, queue monitoring, capacity, failed-job handling and a retention policy for images and diagnostics. |

For a new tenancy, follow [installation and operations](OPERATIONS.md). Replace resource identifiers and wallets with the receiving team's values. Start with one approved synthetic upload and record its job ID, model version and source hash. For an existing deployment, check the active versions against the private release manifest. Use a new batch reference so that earlier demo records are easy to identify.

<a id="uji-bulk-dan-review-di-oci"></a>

## Verify a batch in OCI

1. Open the authorized operator interface. Check **OCI Batam** and the configured model. Upload both images in one action. Record each filename, source hash, case reference and job ID. There should be one job per image. The job detail API and JSON export expose the job ID.
2. Open both saved results and compare household data, member count and row order against the originals. When NIK routing finishes, record the number flagged, cleared automatically and sent to a reviewer. Zero review cases is a valid outcome. To test a decision, use a fresh authorized job with an actual case.
3. Choose **Review NIKs for this card**. Compare the member row, name, crop and original reading. Choose **Confirm as printed**, **Correct NIK** or **Still unreadable**. Record the job and row. Reopen **Reviewed** and verify the latest decision on that same member. The hosted participant demo scopes results to the current browser session. Use the private operator interface for cross-session database acceptance.
4. In SQLcl, connect with read access and run the following from the portable repository root. Replace `JOB_ID` with the real identifier. Run it before and after the decision.

```sql
@sql/check_nik_handover.sql JOB_ID
```

The first query reports the original `MODEL` version and the routing receipt. Once `FINISHED_AT` is populated, `FLAGGED = CLEARED + REVIEW_COUNT`. If it is empty, check the job, router and timer. The second query reports the member row, revision and reviewer action without printing a NIK. After the first decision, look for revision 1 on the expected row and the same original model version.

## Acceptance checks

- Open one case in two tabs at the same revision. After the first tab saves, the second tab's stale submission should be rejected.
- Run the router again for the finished job. Reconcile calls and cases with the previous receipt. If a call has an unknown outcome, inspect the ledger before retrying.
- Retain the application and router tags, scope, approved source and processing destination, both job IDs and the reopen result privately. Capture upload time, last database commit and final routing time separately.
- Before increasing load, test a separately labeled holdout set against the expected peak arrivals and p95 target. A new reread threshold needs its own acceptance results and release decision.
