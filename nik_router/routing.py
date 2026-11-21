"""Field-level review routing; no reference values or sample identifiers."""
from collections import Counter
from datetime import date
import math
import re


def digits(value):
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:[ .-]+[0-9]+)*',value.strip()):
        return None
    v=re.sub(r'[ .-]','',value.strip())
    return v if len(v)==16 else None


def birth(value):
    s=(value or '').strip().casefold()
    try:
        iso=re.fullmatch(r'([0-9]{4})-([0-9]{2})-([0-9]{2})',s)
        if iso:return date(*map(int,iso.groups()))
        m=re.fullmatch(r'([0-9]{1,2})([-./ ])([a-z]+|[0-9]{1,2})\2([0-9]{4})',s)
        if not m:return None
        day,_,month,year=m.groups()
        months='januari februari maret april mei juni juli agustus september oktober november desember'.split()
        lookup={alias:i for i,full in enumerate(months,1) for alias in (full,full[:3])}
        lookup.update({'ags':8,'agt':8,'nop':11,'nopember':11})
        num=int(month) if month.isdigit() else lookup[month]
        return date(int(year),num,int(day))
    except (KeyError,ValueError):return None


def namekey(value):return ' '.join((value or '').upper().split())


def box(poly):
    if not isinstance(poly,list) or len(poly)<3 or any(not isinstance(p,list) or len(p)!=2 or not all(type(x) in (int,float) and math.isfinite(x) for x in p) for p in poly):
        return None
    xs,ys=zip(*poly)
    return (min(xs),min(ys),max(xs),max(ys)) if max(xs)>min(xs) and max(ys)>min(ys) else None


def neighbors(name,number,slope):
    if not name or not number or name[2]>number[0]:return False
    def band(b):
        offset=(b[0]+b[2])*slope/2
        return b[1]-offset,b[3]-offset
    a,b=band(name),band(number)
    return min(a[1],b[1])-max(a[0],b[0])>=.5*(max(a[1],b[1])-min(a[0],b[0]))


OCR_MIN_CONFIDENCE = 0.90

def classify(saved,primary,hybrid,raw_ocr,angle,*,min_confidence=OCR_MIN_CONFIDENCE):
    if not 0 <= min_confidence <= 1:raise ValueError("min_confidence must be between zero and one")
    rows=saved['members']
    def index(read):
        pairs=[(m['row_number'],digits(m['nik_raw'])) for m in read['members']]
        return dict(pairs) if len(dict(pairs))==len(pairs) else {}
    readers=[index(read) for read in (primary,hybrid)]
    counts=Counter(digits(m['nik_raw']) for m in rows)
    names=Counter(namekey(m['full_name']) for m in rows)
    texts=raw_ocr.get('rec_texts',[]);scores=raw_ocr.get('rec_scores',[])
    polys=raw_ocr.get('rec_polys') or [None]*len(texts)
    if not len(texts)==len(scores)==len(polys):raise ValueError('inconsistent OCR data')
    lines=[(namekey(t),digits(t),s,box(p)) for t,s,p in zip(texts,scores,polys)]
    idlines=[l for l in lines if l[1]]
    named=[l for l in lines if l[0] and l[0] in names]
    slope=math.tan(math.radians(angle)) if angle is not None else 0.
    results=[]
    for m in rows:
        n=m['row_number'];v=digits(m['nik_raw']);name=namekey(m['full_name']);dob=birth(m['birth_date_raw'])
        reasons=[]
        if not v:reasons.append('invalid_format')
        elif counts[v]>1:reasons.append('duplicate')
        if v and dob:
            allowed={dob.day,dob.day+40};sex=(m.get('sex') or '').strip().upper()
            if sex in ('L','LAKI-LAKI','LAKI LAKI'):allowed={dob.day}
            if sex in ('P','PEREMPUAN'):allowed={dob.day+40}
            if int(v[6:8]) not in allowed or int(v[8:10])!=dob.month or int(v[10:12])!=dob.year%100:
                reasons.append('birth_conflict')
        structural=bool(reasons)
        if not v or any(read.get(n)!=v for read in readers):reasons.append('model_missing_or_disagrees')
        internal=bool(reasons)
        found=[l for l in idlines if l[1]==v]
        supported=len(found)==1 and found[0][2]>=min_confidence
        if not supported:reasons.append('no_unique_clear_ocr')
        primary_flag=bool(reasons)
        matched=[l for l in named if l[0]==name];bound=False
        if supported and name and names[name]==1 and len(matched)==1 and matched[0][2]>=min_confidence:
            a,b=matched[0][3],found[0][3]
            bound=(neighbors(a,b,slope) and sum(neighbors(a,l[3],slope) for l in idlines)==1
                   and sum(neighbors(l[3],b,slope) for l in named)==1)
        if not bound:reasons.append('no_unique_row_anchor')
        results.append({'row':n,'structural':structural,'internal':internal,'primary':primary_flag,
                        'strict':bool(reasons),'reasons':reasons,'dob_available':dob is not None})
    return results
