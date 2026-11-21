"""Synthetic threshold replay. The reader never gets the answer label."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"nik_router"))
from routing import classify


def evaluate(threshold):
    # Prefix 000000 deliberately marks artificial identifiers.
    cases=[("sharp", "0001", .98, "0001"), ("faint-correct", "0002", .84, "0002"),
           ("faint-wrong", "0003", .82, "0009")]
    review=wrong_pass=correct_review=0
    for alias, emitted, confidence, reference in cases:
        value="000000010100"+emitted
        member=dict(row_number=1,full_name="CONTOH",nik_raw=value,birth_date_raw="01-01-2000",sex="L")
        extraction={"members":[member]}
        signals={"rec_texts":["CONTOH",value],"rec_scores":[.99,confidence],
                 "rec_polys":[[[0,0],[20,0],[20,10],[0,10]],[[25,0],[120,0],[120,10],[25,10]]]}
        flagged=classify(extraction,extraction,extraction,signals,0,min_confidence=threshold)[0]["primary"]
        correct=emitted==reference
        review+=flagged;wrong_pass+=not flagged and not correct;correct_review+=flagged and correct
    return review,wrong_pass,correct_review


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare",type=float,nargs="+",default=[.8,.9])
    for v in parser.parse_args().compare:
        review,miss,false_alarm=evaluate(v)
        print(f"OCR threshold {v:.2f}: {review}/3 to review, {miss} wrong passed, {false_alarm} correct flagged")
