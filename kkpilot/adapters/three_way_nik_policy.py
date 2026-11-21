"""Pure, reference-free NIK proposal. No I/O; returned payload is private.

Current NIK must already be a valid 16-digit value to be changed. Invalid current
values are retained and remain in the full scoring denominator. Ordinal confidence
0.5 is a development-informed argmax heuristic, not calibrated truth; it was fixed
before v2 scoring. NIK confidence remains 0.95 in each engine.
"""
import copy, math, re

CONFIDENCE=0.95
ORDINAL_CONFIDENCE=0.5
def number(x):
    if type(x) is not str:return None
    x=''.join(x.split())
    return x if re.fullmatch(r'[0-9]{16}',x,re.ASCII) else None
def name(x):
    if type(x) is not str:return None
    x=' '.join(x.split())
    if not x or not x.isascii():return None
    return re.sub(r'(?<![A-Z0-9_])([A-Z])\.\s*(?=[A-Z0-9_])',r'\1. ',x.upper())
def box(b):
    return b if type(b) is list and len(b)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in b) and b[0]<b[2] and b[1]<b[3] else None
def inside(b,r):return r[0]<= (b[0]+b[2])/2 <=r[2] and r[1]<= (b[1]+b[3])/2 <=r[3]
def horizontal(a,b):return a[2]<=b[0]
def vertical(a,b,slope):
    ay=slope*(a[0]+a[2])/2;by=slope*(b[0]+b[2])/2
    a0,a1=a[1]-ay,a[3]-ay;b0,b1=b[1]-by,b[3]-by
    return max(0,min(a1,b1)-max(a0,b0))/(max(a1,b1)-min(a0,b0))>=.5
def number_name_alignment(a,b,slope):
    ay=slope*(a[0]+a[2])/2;by=slope*(b[0]+b[2])/2
    a0,a1=a[1]-ay,a[3]-ay;b0,b1=b[1]-by,b[3]-by
    return min(a1,b1)>max(a0,b0) and abs((a0+a1)/2-(b0+b1)/2)<=.5*((a1-a0+b1-b0)/2)
def same_region(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter/min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1]))>=.5
def intersects(a,b):
    return max(a[0],b[0])<min(a[2],b[2]) and max(a[1],b[1])<min(a[3],b[3])
def confident(x):return type(x) in (int,float) and math.isfinite(x) and CONFIDENCE<=x<=1
def ordinal_confident(x):return type(x) in (int,float) and math.isfinite(x) and ORDINAL_CONFIDENCE<=x<=1
VERSION = 'three-way-nik-1.0'


def propose(current,du_lines,du_cells,paddle,geometry,slope):
    if type(current) is not dict or type(current.get('members')) is not list:raise ValueError('ROWS')
    rows=current['members'];ns=[r.get('row_number') for r in rows]
    if any(type(n) is not int or not 1<=n<=100 for n in ns) or len(set(ns))!=len(ns):raise ValueError('ROWS')
    out=copy.deepcopy(current);evidence=[]
    geo=geometry if type(geometry) is dict else {};scale=geo.get('scale');nr=box(geo.get('name_rect'));ir=box(geo.get('identifier_rect'))
    valid=(geo.get('mapping')=='RESIZE_ONLY' and type(scale) in (list,tuple) and len(scale)==2 and all(type(v) in (int,float) and math.isfinite(v) and v>0 for v in scale) and nr is not None and ir is not None and type(slope) in (float,int) and math.isfinite(slope))
    def mapped(items):
        result=[]
        for item in items:
            b=box(item.get('bbox_pixels'))
            if b is not None:result.append((item,[b[0]*scale[0],b[1]*scale[1],b[2]*scale[0],b[3]*scale[1]]))
        return result
    if valid: dl=mapped(du_lines);dc=mapped(du_cells);pl=mapped(paddle)
    for member in out['members']:
        n=member['row_number'];decision={'row_number':n,'status':'HELD','reason':None};evidence.append(decision)
        if not valid:decision['reason']='GEOMETRY_OR_SKEW_UNAVAILABLE';continue
        if number(member.get('nik_raw')) is None:decision['reason']='CURRENT_NIK_INVALID';continue
        target=name(member.get('full_name'))
        if target is None:decision['reason']='NAME_INVALID';continue
        names=[(item,b) for item,b in dl if inside(b,nr) and name(item.get('text'))==target]
        if len(names)!=1:decision['reason']='NAME_NOT_UNIQUE';continue
        name_item,nb=names[0]
        if any(type(item.get('text')) is str and item['text'].strip()==str(n) and box(item.get('bbox_pixels')) is None for item in du_lines+paddle):
            decision['reason']='UNLOCATED_PRINTED_NO_AMBIGUITY';continue
        nos=[b for item,b in dl+pl if type(item.get('text')) is str and item['text'].strip()==str(n) and ordinal_confident(item.get('confidence'))
             and inside(b,nr) and horizontal(b,nb) and number_name_alignment(b,nb,slope) and b[3]-b[1]<=2*(nb[3]-nb[1])]
        if not nos:decision['reason']='PRINTED_NO_NOT_FOUND';continue
        competing=[]
        for item,b in dl+pl:
            text=item.get('text');text=text.strip() if type(text) is str else ''
            if (re.fullmatch(r'[0-9]{1,3}',text,re.ASCII) and 1<=int(text)<=100 and int(text)!=n
                    and ordinal_confident(item.get('confidence')) and inside(b,nr) and horizontal(b,nb)
                    and number_name_alignment(b,nb,slope) and b[3]-b[1]<=2*(nb[3]-nb[1])):
                competing.append(b)
        if competing:decision['reason']='COMPETING_PRINTED_NO';continue
        if any(not same_region(a,b) for a in nos for b in nos):decision['reason']='PRINTED_NO_AMBIGUOUS';continue
        if any(number(item.get('text')) is not None and box(item.get('bbox_pixels')) is None for item in du_lines+du_cells):decision['reason']='UNLOCATED_NIK_AMBIGUITY';continue
        if any(number(item.get('text')) is not None and box(item.get('bbox_pixels')) is None for item in paddle):decision['reason']='UNLOCATED_PADDLE_NIK_AMBIGUITY';continue
        candidates=[(item,b,number(item.get('text'))) for item,b in dl+dc if inside(b,ir) and number(item.get('text')) is not None and vertical(b,nb,slope)]
        if any(item is name_item or intersects(b,nb) for item,b,_ in candidates):decision['reason']='NAME_NIK_ANCHOR_AMBIGUOUS';continue
        vals={v for _,_,v in candidates}
        if len(vals)!=1:decision['reason']='NIK_NOT_UNIQUE';continue
        v=next(iter(vals)); matches=[(i,b) for i,b,x in candidates if x==v]
        if any(number(item.get('text')) is not None and number(item.get('text'))!=v and inside(b,ir) and vertical(b,nb,slope) for item,b in pl):
            decision['reason']='PADDLE_COMPETING_NIK';continue
        # Disjoint same-value regions remain ambiguous; duplicate cell/line geometry may collapse.
        if any(not same_region(a[1],b[1]) for a in matches for b in matches):decision['reason']='NIK_REGION_AMBIGUOUS';continue
        if not any(confident(i.get('confidence')) for i,b in matches):decision['reason']='DU_CONFIDENCE';continue
        supported=any(confident(p.get('confidence')) and number(p.get('text'))==v and any(same_region(pb,db) and confident(di.get('confidence')) for di,db in matches) for p,pb in pl)
        if not supported:decision['reason']='PADDLE_SUPPORT_OR_CONFIDENCE';continue
        if number(member['nik_raw'])==v:decision.update(status='UNCHANGED',reason='SAME_DIGITS');continue
        member['nik_raw']=v;decision.update(status='PROPOSED',reason='DU_PADDLE_CORROBORATED')
    return out,evidence


def _rows(payload):
    if type(payload) is not dict or type(payload.get('members')) is not list:
        raise ValueError('ROWS')
    rows = payload['members']
    if not rows or len(rows) > 100 or any(type(row) is not dict for row in rows):
        raise ValueError('ROWS')
    numbers = [row.get('row_number') for row in rows]
    if any(type(n) is not int or not 1 <= n <= 100 for n in numbers) or len(set(numbers)) != len(numbers):
        raise ValueError('ROWS')
    return {row['row_number']: row for row in rows}


def gate(base, dual, population, decisions):
    """Apply eligible proposals only on exact raw ASCII16 independent agreement.

    Population includes every model-reported row; never trim it to the old rowset.
    Metadata and all fields other than nik_raw retain their prior values.
    """
    b, d, p = _rows(base), _rows(dual), _rows(population)
    if set(b) != set(d) or set(b) != set(p):
        raise ValueError('ROWS')
    if type(decisions) is not list or len(decisions) != len(b):
        raise ValueError('DECISIONS')
    index = {}
    for event in decisions:
        if type(event) is not dict or type(event.get('row_number')) is not int or event['row_number'] in index:
            raise ValueError('DECISIONS')
        index[event['row_number']] = event
    if set(index) != set(b):
        raise ValueError('DECISIONS')
    # A proposal must not smuggle changes in other fields or metadata.
    permitted = copy.deepcopy(dual)
    for row in permitted['members']:
        row['nik_raw'] = b[row['row_number']].get('nik_raw')
    if permitted != base:
        raise ValueError('SCOPE')
    result, audit = copy.deepcopy(base), []
    for member in result['members']:
        n = member['row_number']; event = index[n]
        candidate, previous, observed = d[n].get('nik_raw'), b[n].get('nik_raw'), p[n].get('nik_raw')
        eligible = event.get('status') == 'PROPOSED' and event.get('reason') == 'DU_PADDLE_CORROBORATED'
        if (candidate != previous) != eligible:
            raise ValueError('ELIGIBILITY')
        exact = bool(eligible and type(candidate) is str and re.fullmatch(r'[0-9]{16}', candidate, re.ASCII)
                     and candidate == observed)
        if exact:
            member['nik_raw'] = candidate
        status = 'APPLIED' if exact else 'UNCHANGED' if event.get('status') == 'UNCHANGED' else 'HELD'
        reason = 'THREE_WAY_EXACT_RAW' if exact else 'NIK_READ_DISAGREEMENT' if eligible else event.get('reason')
        audit.append({'row_number': n, 'status': status, 'reason': reason,
                      'dual_eligible': eligible, 'population_exact_match': exact})
    return result, audit


def project(base, du_lines, du_cells, paddle, geometry, slope, population):
    _rows(base)
    for items in (du_lines, du_cells, paddle):
        if type(items) is not list or len(items) > 20000 or any(type(item) is not dict for item in items):
            raise ValueError('EVIDENCE')
    dual, decisions = propose(base, du_lines, du_cells, paddle, geometry, slope)
    return gate(base, dual, population, decisions)
