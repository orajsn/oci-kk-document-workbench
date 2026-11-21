"""Locate NIK cells without a per-image profile, using existing OCR geometry."""
import math
from collections import Counter
from PIL import Image
from routing import box, namekey, neighbors
import re


def find_rows(image, raw, members, angle):
    names={m['row_number']:namekey(m.get('full_name')) for m in members}
    frequencies=Counter(names.values())
    texts=raw.get('rec_texts',[]);polys=raw.get('rec_polys') or [];scores=raw.get('rec_scores',[])
    if not len(texts)==len(polys)==len(scores):return {}
    lines=[]
    for text,poly,score in zip(texts,polys,scores):
        b=box(poly)
        if b and 0<=b[0]<b[2]<=image.width and 0<=b[1]<b[3]<=image.height:
            lines.append((namekey(text),b,score))
    headers=[b for text,b,s in lines if re.sub(r'[ .:]','',text)=='NIK' and s>=.8]
    if len(headers)!=1:return {}
    header=headers[0];x=(header[0]+header[2])/2
    # Long numeric strings in the printed NIK column, independently of what the
    # worker stored. A horizontal overlap alone cannot establish a member match.
    numeric=[b for t,b,s in lines if b[1]>header[3] and b[0]<=x<=b[2] and
             re.fullmatch(r'[0-9][0-9 .-]*[0-9]',t) and 8<=len(re.sub(r'\D','',t))<=18]
    named=[(t,b) for t,b,s in lines if t and frequencies[t]==1 and b[1]>header[3] and s>=.90 and b[2]<x]
    slope=math.tan(math.radians(angle or 0));hits={}
    for n,name in names.items():
        ids=[b for t,b in named if t==name]
        if len(ids)!=1:continue
        a=ids[0];bs=[b for b in numeric if neighbors(a,b,slope)]
        if len(bs)!=1 or sum(neighbors(c,bs[0],slope) for _,c in named)!=1:continue
        hits[n]=(a,bs[0])
    # Require at least two aligned row witnesses and correct vertical ordering.
    ordered=[v[1] for _,v in sorted(hits.items())]
    if len(ordered)<2 or any(b[1]<a[3] for a,b in zip(ordered,ordered[1:])):return {}
    result={}
    for n,(a,b) in hits.items():
        # Use the aligned token envelope, including side bearings, without borrowing
        # any digits from another row or supplementing source pixels.
        left=max(0,math.floor(min(c[0] for c in ordered))-2);right=min(image.width,math.ceil(max(c[2] for c in ordered))+2)
        t=max(0,math.floor(b[1])-1);bottom=min(image.height,math.ceil(b[3])+1)
        if any(k!=n and c[1]<bottom and c[3]>t for k,(_,c) in hits.items()):continue
        tile=image.crop((left,t,right,bottom))
        name=image.crop((math.floor(a[0]),math.floor(a[1]),math.ceil(a[2]),math.ceil(a[3])))
        context=Image.new('RGB',(name.width+12+tile.width,max(tile.height,name.height)),'white')
        context.paste(name,(0,0));context.paste(tile,(name.width+12,0))
        result[n]=(tile,context)
    return result
