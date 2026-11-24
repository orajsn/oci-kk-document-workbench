# Recover a missing form-serial prefix

A family card can have a printed serial with a letter prefix. The extractor sometimes reads the digits but drops the letters. This staged module checks the number label, prefix and digits separately on the same printed line before the caller saves `household.form_serial`.

## When the rule applies

The initial stored value, primary extraction and hybrid reading must agree on the digits, including leading zeros. Page OCR must identify a single adjacent label and number in the left header. The caller must bind that position back to the original pixels for the same job.

Local CPU OCR reads each crop at native size and at 3× using bicubic scaling. Both label reads must identify a number label. Both prefix reads must exactly match the letters found by page OCR, and both digit reads must match the original number. If a condition fails or the field already carries a review flag, the existing result is returned. Member NIKs follow their own review path.

## Test and integrate

Run `python -m unittest -v test_serial_agreement` in this directory with Python 3.9 or later. Decision and geometry tests use the standard library; pixel cropping uses Pillow and NumPy.

1. Call `split_serial.pair(saved, primary, hybrid, ocr, frame)`. The OCR coordinates must refer to that frame. See `test_serial_agreement.py` for the input shape.
2. Verify the source, transform, job and coordinates. Stop if a perspective correction cannot be mapped back to original pixels. Take the label and prefix regions from `prefix_cells.cut(native_image, proposal['label_box'])` and the number from `proposal['number_box']`.
3. Read each crop at native size and at 3× without a reference answer. Pass `reads` with keys `label`, `series` and `number`. Each holds two `{'text': ...}` objects in that scale order.
4. Call `serial_agreement.apply(saved, proposal, reads)` before persistence. The calling application owns review routing and the Oracle commit. Keep the original reading, source identity and crop evidence privately for audit.

Both crop sizes use the same source and OCR engine. If the page and both crops misread the same letter, the rule can accept a wrong prefix. Test other printed prefixes on new documents before enabling the rule more broadly. This module makes no Gemini calls.
