"""Generate and round-trip a synthetic, geometry-only focus manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kkpilot.adapters.focused_source_views import prepare
from kkpilot.adapters.gemini_focused_contract import ROLES


def main(output):
    source=ROOT/"demo/synthetic-kk.png"
    original=source.read_bytes()
    sha=lambda value:hashlib.sha256(value).hexdigest()
    with Image.open(source) as loaded: image=loaded.convert("RGB")
    width,height=image.size
    views=[]
    for role, box in zip(ROLES,([0,0,width//2,height],[width//2,0,width,height])):
        tile=image.crop(box)
        views.append(dict(name=role,pixel_rect=box,size=list(tile.size),pixel_sha256=sha(tile.tobytes())))
    record=dict(source_sha256=sha(original),approved_policy={"crop":None,"rotate":0},
                canonical_size=list(image.size),canonical_pixel_sha256=sha(image.tobytes()),
                views=views,source_coverage_reviewed=True,review_record_sha256=sha(b"synthetic geometry fixture"))
    manifest=dict(version="kk-focused-source-geometry-1.0",fields=["full_name","birth_place"],
                  records=[record],reference_values_included=False)
    blob=json.dumps(manifest,indent=2).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists() and output.read_bytes()!=blob:raise ValueError("Existing manifest differs")
    output.write_bytes(blob)
    crops=prepare(original,sha(original),{},str(output),sha(blob))
    print(f"{len(crops)} verified synthetic crops; manifest sha256 {sha(blob)}")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    main(parser.parse_args().output)
