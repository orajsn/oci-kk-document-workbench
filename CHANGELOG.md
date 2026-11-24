# Changelog

## 0.4.3 · 2026-11-24

The public workbench, browser demo and engineering guides now use English. The first-batch guide follows one fictional member from the printed NIK to the stored correction and explains how to test a reread threshold on the same batch. The hosted participant pilot is managed separately.

## 0.4.2 · 2026-11-24

Bulk upload distinguishes accepted files, failed submissions and uncertain delivery. Once a record is saved, the card shows how many NIKs were routed to reviewers. A review case explains why it was flagged and which part of the print to compare. If a differing position is known, the UI identifies the digit from the left. Reviewers can still open the original diagnostic details.

## 0.4.1 · 2026-11-24

Documentation update for the 0.4.0 application. The README opens the two-card demo. The engineering walkthrough covers upload, correction, restart and threshold replay.

## 0.4.0 · 2026-11-22

Introduced Indonesian labels for intake and NIK review in this release. The document list shows saved member counts and switches to cards on phones. The crop and extracted NIK are displayed together.

## 0.3.0 · 2026-11-21

Added a two-image local batch with persistent review history, a restart check and a reread-threshold comparison. Readings, OCR signals and print references are separate. The default threshold is 0.90. The local exercise uses SQLite and the Python standard library. The private handover included the portable exercise for offline use.

## 0.2.0 · 2026-11-21

Added full-card evaluation. It reports readable values, blanks, fields absent from a layout, unknown references and cards that match in full. The synthetic two-member example shows how one difference affects the document score.

## 0.1.0 · 2026-11-21

Initial portable release for bulk intake and NIK review. Each image gets a job and receipt, with repeat-submission handling. The worker supports local OCR and an OCI Generative AI adapter. After commit, flagged NIK crops can be reread and reviewed per member, with the initial extraction retained. Legacy print comparisons preserve the printed digits. Operators supply their compartment, model routing profile and private geometry. The public examples use fictional data.
