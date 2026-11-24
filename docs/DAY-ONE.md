# Upload two family cards and verify one correction

This exercise takes two fictional family cards through the same local UI, worker, review store and routing function that ship in this repository. One digit in card B is deliberately wrong. You will correct it, restart the app, and use the unchanged original reading to test which NIKs should be read a second time.

You need Python 3.11 or later and a browser. The reader replays fixed OCR and extraction data. This exercise uses SQLite and makes no model calls.

## Start the local workspace

If you received a portable handover ZIP, extract it and open a terminal in the `portable-edition` directory containing `run_local.py`. For a fresh clone, use the release tag.

```sh
git clone --branch v0.4.4 --depth 1 https://github.com/orajsn/oci-kk-document-workbench.git
cd oci-kk-document-workbench
```

```sh
python --version
python run_local.py --port 8876 --data-dir runtime/day-one --reference-dir demo/day-one/reader
```

Use `py -3` on Windows or `python3` on macOS/Linux if needed. Use that interpreter throughout. Leave this terminal running and open [the local workspace](http://127.0.0.1:8876). Look for **Local practice** and **Recorded sample**. Port 8876 is for this exercise. Port 8765 is the private OCI operator example.

## Upload the two cards

1. Choose **Upload cards**, enter `LATIHAN` as the **Batch reference**, and select **Recorded sample**.
2. Select `demo/day-one/kartu-a.png` and `kartu-b.png` together, then choose **Upload files** once.
3. Select **View uploads**. Wait until both records show **Needs review**. The case references end in `-001` and `-002`. Use the filenames to tell the cards apart.
4. In a second terminal, from the same directory, check the batch.

```sh
python examples/check_day_one.py --batch LATIHAN
```

Record both job IDs. At this point the original and saved readings each match **2 of 3 NIKs** against the fictional print.

## Correct card B and reopen it

1. Open `kartu-b.png`, select **Members**, and inspect row 2. The printed NIK ends in **0009**. The extraction ends in **0003**.
2. Change just the last digit to **9**, preserving any leading zeros. Add a short **Reviewer note**. Keep the document at **Needs review** because you have checked only one field. Select **Save review**.
3. Close the browser tab. Stop the server with Ctrl+C and restart it with the **same data directory**. Open the app in a private browser window and revisit card B. The second member's NIK still ends in **9**.
4. Choose **Download JSON**. The original NIK in `review_history[0].previous_extraction.members[1].nik_raw` ends in **3**. The corrected NIK in `extraction.members[1].nik_raw` ends in **9**. Array index `1` is the second member.

```sh
python examples/check_day_one.py --batch LATIHAN --require-review
```

Both job IDs should match your earlier notes. Card A has no review, card B has one review and one changed NIK. The original score remains **2/3** and the saved score is **3/3**. The correction is stored in `runtime/day-one/pilot.sqlite3`.

## Test when to reread a NIK

Use the original readings from the same batch.

```sh
python examples/check_day_one.py --batch LATIHAN --require-review --compare 0.83 0.80
```

| Minimum OCR score | NIKs sent for rereading | Wrong NIKs that passed | Correct NIKs sent for rereading |
| --- | ---: | ---: | ---: |
| 0.90 (default) | 2 | 0 | 1 |
| 0.83 | 1 | 0 | 0 |
| 0.80 | 0 | 1 | 0 |

In this fixture the correct reading scores 0.84 and the wrong one scores 0.82. At 0.83 only the wrong NIK goes for rereading. Try `--compare 0.85` and both are sent again. These counts measure calls proposed by the router. The number of cases a person must review depends on what the reread returns. Replaying the threshold does not alter the saved record.

The parameter is `min_confidence` in [`classify`](../nik_router/routing.py). The post-save router uses `OCR_MIN_CONFIDENCE`. To change the default, test on a separate branch, run regression checks, and repeat on a held-out set with verified labels before proposing a release. Keep the last accepted tag for rollback.

## If the result differs

| Symptom | What to check |
| --- | --- |
| **Recorded sample** is unavailable | Start from the repository root with `--reference-dir demo/day-one/reader`. That directory must contain two `KK-*.json` files. |
| `REFERENCE_NOT_FOUND` | Use the supplied PNGs. Resaving an image can change its hash. |
| The checker cannot find two jobs | Wait for the worker, then check the case references. If `LATIHAN` is already in use, submit a fresh reference such as `LATIHAN2` and use `--batch LATIHAN2` in the checker. |
| Port 8876 is occupied | Use another port in the server command, browser URL and checker, for example `--port 8877`. |
| The list is empty after restart | Check the working directory and the location of `runtime/day-one/pilot.sqlite3`. |
| The saved score is still 2/3 | Reopen card B, verify row 2 and inspect **Download JSON** for a saved review event. |

For the cloud-backed member review queue and database checks, continue with [OCI acceptance](HANDOVER.md#verify-a-batch-in-oci). For accuracy on new images, use an authorized dataset with a separate evaluator. [Field tuning guide](TUNING.md)
