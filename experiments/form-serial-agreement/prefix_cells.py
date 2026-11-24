"""Locate a separating gap from dark print, without using the expected text as a pixel template."""
from itertools import groupby
from PIL import ImageOps
import math,re
import numpy as np

def cut(native,box):
    left,top,right,bottom=map(int,box)
    if not (0<=left<right<=native.width and 0<=top<bottom<=native.height):return None
    label=native.crop((left,top,right,bottom)).convert('RGB')
    grey=np.asarray(ImageOps.grayscale(label));height,width=grey.shape
    if height<8 or width<12:return None
    hist=np.bincount(grey.ravel(),minlength=256).astype(float)/grey.size
    weight=hist.cumsum();moment=(hist*np.arange(256)).cumsum()
    between=(moment[-1]*weight-moment)**2/(weight*(1-weight)+1e-9)
    dark=grey<=int(between.argmax())
    significant=(dark.sum(axis=0)>=max(2,math.ceil(height*.08)))
    nonzero=np.flatnonzero(significant)
    if len(nonzero)<4:return None
    x0,x1=int(nonzero[0]),int(nonzero[-1])+1
    gaps=[]
    for empty,group in groupby(enumerate(~significant),lambda p:bool(p[1])):
        cols=[p[0] for p in group]
        if not empty:continue
        a,b=cols[0],cols[-1]+1
        if a-x0<.44*height or x1-b<.44*height or b-a<max(2,.12*height):continue
        gaps.append((b-a,a,b))
    gaps.sort(reverse=True)
    if not gaps or len(gaps)>1 and gaps[0][0]<=gaps[1][0]+1:return None
    _,a,b=gaps[0];split=(a+b)//2
    def tile(xstart,xend):
        part=grey[:,xstart:xend]
        ink=dark[:,xstart:xend];ys,xs=np.nonzero(ink)
        if len(xs)<4:return None
        # Retain the original image intensities and leave a margin on all sides.
        l=left+xstart+int(xs.min());rr=left+xstart+int(xs.max())+1
        t=top+int(ys.min());bb=top+int(ys.max())+1
        margin=max(2,math.ceil(.15*(bb-t)))
        lo=left if xstart==0 else left+split
        hi=left+split if xstart==0 else right
        bounds=(max(lo,l-margin),max(0,t-margin),min(hi,rr+margin),min(native.height,bb+margin))
        return native.crop(bounds),bounds
    lab,ser=tile(0,split),tile(split,width)
    if not lab or not ser:return None
    return lab,ser
