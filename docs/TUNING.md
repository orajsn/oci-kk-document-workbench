# Diagnose an extraction error

Start with the printed field and the member row it belongs to. A correct sequence of digits on the wrong row is a geometry problem. If the row is correct, compare the OCR reading, the model response and the stored field. That comparison tells you where to make the change.

| Symptom | Inspect | What to test |
| --- | --- | --- |
| The image is rotated or a crop cuts through a field | [Image preparation](../kkpilot/adapters/document_preprocess.py), [focused views](../kkpilot/adapters/focused_source_views.py) | Compare original size and resize using the same source pixels. Verify orientation, hashes and whether the neighboring row enters the crop. |
| A NIK belongs to another member | [Local OCR](../kkpilot/adapters/local_paddle_ocr.py), [row locator](../nik_router/locator.py) | Find the NIK column from its header and match its vertical position with the name. Send uncertain row assignments to review. |
| The print is legible but Gemini returns the wrong field or format | [Extraction contract](../kkpilot/adapters/gemini_contract.py), [focused contract](../kkpilot/adapters/gemini_focused_contract.py), [OCI adapter](../kkpilot/adapters/oci_gemini.py) | Inspect the prompt and schema used by that adapter. Preserve raw values, meaningful punctuation and member order. Update the parser and tests together if the schema changes. |
| Too many NIKs are reread, or a wrong NIK passes | [Routing classifier](../nik_router/routing.py) | Vary `min_confidence` in `classify`. The default `OCR_MIN_CONFIDENCE` is 0.90. Count both missed errors and correct NIKs sent for rereading. |
| The crop reading differs from the stored number | [Rereader](../nik_router/reread.py), [router service](../nik_router/service.py), [legacy print rules](../nik_router/legacy_print.py) | The rereader sees the crop without the saved digits, birth date or reference answer. Keep unresolved structural conflicts visible. A 13-digit legacy print requires an eligible layout, a unique number, a recurring legacy pattern and a clear matching read. |
| A reviewer cannot decide on a digit | [Review persistence](../kkpilot/cloud/nik_review.py), [review UI](../web/nik-review.js) | Offer original size, zoom and row context. Save the outcome as a revision. Verify a correction and its permitted use before treating it as an evaluation label. |

## Measure a change

1. Pick a failing field. Write down the suspected cause and expected outcome before changing the code. Include the neighboring row and previously correct fields as controls.
2. Separate development images from the final holdout. Include old and new layouts, camera angles, lighting and unclear print. Keep images and labels private. Freeze source hashes, comparison rules, unknown labels, prompt version and model. Only the evaluator reads the reference.
3. Lock predictions before scoring. Report matches out of all field slots, matches out of readable values, and cards with every slot matched. Count unknown references, incorrect NIKs that passed and human reviews separately. Two readers can agree on the same misread pixel.
4. Use job receipts to distinguish queue time, extraction, rereading, database commit and final routing. Record new calls, input/output tokens, retries and errors. Identify reused outputs when reporting replay cost or latency.
5. Recommend a release when the gain repeats on held-out images, the same members retain their data and previously correct critical fields do not regress. Record the rollback configuration. If the image remains ambiguous, request review or a clearer source.

## Try the NIK threshold locally

`python examples/tuning_lab.py --compare 0.80 0.90` runs the same classifier as the service on three fictional members. Labels are consulted after prediction. At 0.80 one wrong NIK passes. At 0.90 that error is caught and one correct number also goes for rereading. To replay actual local jobs, use the [first-batch exercise](DAY-ONE.md). It compares the original extraction even after a correction has been saved.

## Score a whole card

```sh
python examples/score_documents.py --reference examples/synthetic-reference.json --predictions examples/synthetic-prediction.json
```

The fixture contains 12 household fields and 18 fields per member. With two members, that is 48 slots. One prediction deliberately differs, giving **47/48 fields matched** and **0/1 cards fully matched**.

Join prediction and reference by document ID and source SHA-256. A field can be `readable`, `blank`, `absent` or `unknown`. Empty fields remain present with JSON `null`. Check `normalized()` for the date and character comparison rules and record any policy change before comparing releases. Keep the answer key separate from extraction.
