"""Pure image preparation: retain original, optionally derive safe table views."""
import base64, hashlib, json, math
from io import BytesIO
from PIL import Image

LIMIT_IMAGE=5*1024*1024
LIMIT_REQUEST=8*1024*1024

def boxes(raw,w,h):
    try:
        pages=raw['pages']
        if len(pages)!=1 or len(pages[0]['tables'])!=2: return None
        out=[]
        for t in pages[0]['tables']:
            v=t['bounding_polygon']['normalized_vertices']
            if len(v)<4: return None
            pts=[(p['x'],p['y']) for p in v]
            if any(type(x) not in (int,float) or type(y) not in (int,float) or not math.isfinite(x) or not math.isfinite(y) or not (0<=x<=1 and 0<=y<=1) for x,y in pts): return None
            x1,y1=min(x for x,y in pts),min(y for x,y in pts)
            x2,y2=max(x for x,y in pts),max(y for x,y in pts)
            if x2-x1<.35 or y2-y1<.025: return None
            out.append((x1,y1,x2,y2))
        out.sort(key=lambda b:b[1])
        if out[0][3]>out[1][1]: return None
        boundary=math.floor((out[0][3]+out[1][1])*h/2)
        result=[]
        for i,(x1,y1,x2,y2) in enumerate(out):
            rect=(max(0,math.floor((x1-.02)*w)),max(0,math.floor((y1-.01)*h)),min(w,math.ceil((x2+.02)*w)),min(h,math.ceil((y2+.01)*h)))
            rect=(rect[0],rect[1] if i==0 else max(rect[1],boundary),rect[2],min(rect[3],boundary) if i==0 else rect[3])
            if not (rect[0]<=math.floor(x1*w) and rect[1]<=math.floor(y1*h) and rect[2]>=math.ceil(x2*w) and rect[3]>=math.ceil(y2*h) and rect[2]>rect[0] and rect[3]>rect[1]): return None
            result.append(rect)
        return result
    except (KeyError,TypeError,ValueError,OverflowError): return None

def prepare(image,raw):
    if not 0<len(image)<LIMIT_IMAGE: raise ValueError('ORIGINAL_IMAGE_LIMIT')
    with Image.open(BytesIO(image)) as source:
        source.load(); w,h=source.size
        rects=boxes(raw,w,h)
        views=[image]
        meta=[{'role':'original','dimensions':[w,h],'bytes':len(image),'sha256':hashlib.sha256(image).hexdigest()}]
        if rects:
            for role,rect in zip(('upper','lower'),rects):
                crop=source.convert('RGB').crop(rect)
                if max(crop.size)>4096:
                    scale=4096/max(crop.size)
                    crop=crop.resize(tuple(max(1,round(v*scale)) for v in crop.size),Image.Resampling.LANCZOS)
                buf=BytesIO();crop.save(buf,format='JPEG',quality=92,subsampling=0,optimize=False,progressive=False)
                b=buf.getvalue()
                if len(b)>=LIMIT_IMAGE: raise ValueError('CROP_IMAGE_LIMIT')
                views.append(b);meta.append({'role':role,'crop':list(rect),'dimensions':list(crop.size),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
        return views,meta

def attach(oci,request,views):
    m=oci.generative_ai_inference.models
    content=request.chat_request.messages[1].content
    expected='data:image/jpeg;base64,'+base64.b64encode(views[0]).decode('ascii')
    if len(content)!=2 or content[1].image_url.url != expected: raise ValueError('ORIGINAL_IMAGE_MISMATCH')
    if any(not 0<len(v)<LIMIT_IMAGE for v in views): raise ValueError('IMAGE_LIMIT')
    # Existing original stays untouched; add only independently derived views.
    for image in views[1:]:
        content.append(m.ImageContent(image_url=m.ImageUrl(url='data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii'),detail='AUTO')))
    # Conservative JSON serialization plus 64 KiB envelope for transport variance.
    size=len(json.dumps(oci.util.to_dict(request),ensure_ascii=True).encode('utf-8'))
    if size+65536>=LIMIT_REQUEST: raise ValueError('REQUEST_SIZE_LIMIT')
    return size
