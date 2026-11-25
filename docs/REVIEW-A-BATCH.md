# Review a batch in the OCI workspace

Upload one image per family card. The batch summary counts saved extractions separately from NIKs waiting for an operator. A card can have a saved result, two household members, and one open NIK. A decision about that NIK leaves the card's overall sign-off with the reviewer.

## From the batch to the printed row

1. Open the last upload. **Review a NIK** opens the first pending case in reference and row order. The link on a document row opens that document's first pending NIK.
2. Check the batch reference, member row, and name against the **Member row** photograph. If the extraction lacks a unique name for that row, use the printed row and the row number.
3. Switch to **NIK digits** for a larger crop. Zoom and contrast affect only the view. Return to **Member row** at any point.
4. Choose **Confirm as printed**, **Correct NIK**, or **Still unreadable**. A correction must reproduce the print, including leading zeros. An unreadable decision records the need for a clearer image.
5. The case stays on screen after saving. Reopen its link in a new tab to see the decision alongside the original AI value. **Next case** checks the remaining queue. In the original batch tab, it continues into the next document. A standalone case link stays within its document.

A stale tab must reload the latest decision before saving a different one. If the result of a save is uncertain, retry the same action so that the original request can be reconciled.

## Read a whole card

**Open result** shows the head of household, card number and a table with one row per member. The underlying editor retains every field. **Show field checks** expands routine readability controls. Ambiguous and unreadable fields remain visible in the compact view.

The Oracle operator interface needs the NIK review tables and read grants for queue counts and case links. When those optional tables are unavailable, the list still shows the documents and reports that the NIK count is unavailable. The local two-card exercise uses SQLite and its own recorded sample flow.
