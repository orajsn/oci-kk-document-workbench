"""Detect a serial label split from its numeric part, using geometry and source text only."""
import math
import re

LABEL=re.compile(r'(?:NO(?:MOR)?)[.,:\s]+([A-Z]{1,4})[.:]?\Z',re.ASCII|re.I)
NUMBER=re.compile(r'[0-9]{5,12}\Z',re.ASCII)
RESERVED={'KK','NIK','NIP','RT','RW','KODE','KTP','SERI','PAGE','AKTA'}
def prefix(text):
    if not isinstance(text,str) or not text.isascii():return None
    m=LABEL.fullmatch(text.strip())
    if m is None or m[1].upper() in RESERVED:return None
    return m[1].upper()

def adjacent(label,number):
    """Boxes can overlap at their padded edges, but their centers must be separate and ordered."""
    left,top,right,bottom=label
    l,t,r,b=number
    h=min(bottom-top,b-t)
    return ((left+right)/2<l and right<(l+r)/2 and -.75*h<=l-right<=2*h
            and min(bottom,b)-max(top,t)>=.60*h
            and abs(top+bottom-t-b)/2<=.45*max(bottom-top,b-t))

def pair(saved,primary,hybrid,ocr,frame):
    if not isinstance(ocr,dict) or not isinstance(frame,(tuple,list)) or len(frame)!=2:
        return None
    values=[doc['household'].get('form_serial') for doc in (saved,primary,hybrid)]
    if not all(isinstance(v,str) and NUMBER.fullmatch(v.strip()) for v in values):return None
    number=values[0].strip()
    if any(v.strip()!=number for v in values[1:]):return None
    other=[saved['household'].get('kk_number_raw')]+[m.get('nik_raw') for m in saved['members']]
    if any(re.sub(r'[^0-9]','',v or '')==number for v in other):return None
    width,height=frame
    if any(type(v) is not int or v<=0 for v in frame):return None
    data=[ocr.get(k) for k in ('rec_texts','rec_scores','rec_polys')]
    if not all(isinstance(v,list) for v in data) or len({len(v) for v in data})!=1:return None
    labels=[];nums=[];lines=[];numeric_boxes=[]
    for idx,(text,conf,poly) in enumerate(zip(*data)):
        if not isinstance(text,str) or type(conf) not in (float,int) or not .0<=conf<=1 or not isinstance(poly,list) or len(poly)!=4:continue
        if not all(isinstance(p,(list,tuple)) and len(p)==2 and all(type(q) in (int,float) and math.isfinite(q) for q in p) for p in poly):return None
        xs,ys=zip(*poly);l,t,r,b=min(xs),min(ys),max(xs),max(ys)
        if not 0<=l<r<=width or not 0<=t<b<=height:return None
        if b>height*.25 or r>width*.60:continue
        box=(l,t,r,b)
        if any(c.isalnum() for c in text):lines.append((idx,box))
        pre=prefix(text)
        if pre:labels.append((idx,pre,conf,box))
        if NUMBER.fullmatch(text.strip()):numeric_boxes.append((idx,box))
        if text.strip()==number:nums.append((idx,conf,box))
    if len(nums)!=1 or nums[0][1]<.90:return None
    ni,_,nb=nums[0];l,t,r,b=nb
    choices=[]
    for idx,pre,conf,(left,top,right,bottom) in labels:
        lb=(left,top,right,bottom)
        if not adjacent(lb,nb) or any(i!=ni and adjacent(lb,other_box) for i,other_box in numeric_boxes):continue
        if any(i not in (idx,ni) and right<(x0+x1)/2<l and min(y1,b,bottom)>max(y0,t,top) for i,(x0,y0,x1,y1) in lines):continue
        choices.append(dict(prefix=pre,number=number,label_box=(left,top,right,bottom),number_box=nb,label_conf=conf))
    if len(choices)!=1 or choices[0]['label_conf']<.85:return None
    return choices[0]
