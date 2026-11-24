# Keep each crop on the correct member row

A sharply read NIK can still belong to the wrong person. Check the crop against the original image before diagnosing an OCR or model error. Apply the recorded EXIF orientation, crop and rotation, then verify the table boundaries, printed row number and pixel hash.

## Run the geometry example

```sh
python examples/make_focus_fixture.py --output runtime/focus-example.json
```

The example creates two regions, calculates their hashes and validates them with the application's manifest reader. Its coordinates apply only to that fixture. For a new layout, inspect every member on authorized images and store the manifest and its SHA-256 privately. Keep `reference_values_included` set to `false`.

## Which manifest controls the crop?

| File or contract | What it binds |
| --- | --- |
| `kk-focused-source-geometry-1.0` | One record per source hash. Records the transform, canonical dimensions and pixel hash, followed by `name_table` and `middle_member_table`. Each box uses `[left, top, right, bottom]` in transformed-image pixels, plus its crop size and pixel hash. `review_record_sha256` links to the geometry review. |
| `nik_router/crop-profiles.json` | Associates a source hash with its alias, transform, `canonical_sha`, NIK and name column boxes, and a member box relative to the NIK column. The two columns must share the same vertical extent. Without a profile, the router looks for the NIK header and matches names to OCR rows on the verified frame. An uncertain row goes to review. |
| `paddle-server-models-1.0` | The `files` mapping pins each relative model file to its SHA-256. Verify the source and version of the weights, then hash `inference.json`, `inference.yml` and `inference.pdiparams` for each model. The configuration also pins the manifest itself. |

Use the same source pixels and transform when comparing native size and zoom. Count a crop on the neighboring row as a member-assignment error even if every digit looks plausible.
