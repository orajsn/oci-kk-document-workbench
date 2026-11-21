"""Source-only grid tilt estimator; never consumes OCR or references."""
import math, statistics

VERSION='grid-angle-1.0'
MAX_ANGLE=8.0
MAX_MAD=0.25

def estimate_segments(segments,width,height):
    if width<=0 or height<=0: raise ValueError('IMAGE_SIZE')
    candidates=[]
    for x1,y1,x2,y2 in segments:
        dx=x2-x1;dy=y2-y1
        if dx==0 or math.hypot(dx,dy)<0.25*width: continue
        angle=math.degrees(math.atan(dy/dx))
        if abs(angle)<=MAX_ANGLE:
            slope=dy/dx;intercept=(y1+y2)/2-slope*(x1+x2)/2
            candidates.append((angle,intercept))
    result={'status':'UNAVAILABLE','angle_degrees':None,'slope':None,'segments':len(candidates),'inlier_segments':0,'distinct_intercepts':0,'angle_mad_degrees':None,'vertical_span_fraction':None}
    if not candidates:return result
    median=statistics.median(a for a,_ in candidates)
    mad=statistics.median(abs(a-median) for a,_ in candidates)
    inliers=[(a,y) for a,y in candidates if abs(a-median)<=MAX_MAD]
    offsets=sorted(y for _,y in inliers);groups=[];tolerance=max(3.0,0.005*height)
    for y in offsets:
        if not groups or y-statistics.median(groups[-1])>tolerance: groups.append([y])
        else: groups[-1].append(y)
    centers=[statistics.median(group) for group in groups]
    span=(max(centers)-min(centers))/height if len(centers)>1 else 0.0
    result.update(inlier_segments=len(inliers),distinct_intercepts=len(groups),angle_mad_degrees=mad,vertical_span_fraction=span)
    if abs(median)>MAX_ANGLE or mad>MAX_MAD or len(inliers)<6 or len(groups)<3 or span<0.2:return result
    angle=statistics.median(a for a,_ in inliers)
    result.update(status='AVAILABLE',angle_degrees=angle,slope=math.tan(math.radians(angle)))
    return result

def estimate(prepared):
    import cv2, numpy as np
    cv2.setNumThreads(2)
    image=cv2.imdecode(np.frombuffer(prepared,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    if image is None: raise ValueError('IMAGE_DECODE')
    height,width=image.shape
    if width*height > 25_000_000: raise ValueError('IMAGE_TOO_LARGE')
    edges=cv2.Canny(image,50,150)
    detected=cv2.HoughLinesP(edges,1,math.pi/1800,threshold=max(20,int(width*0.15)),minLineLength=int(width*0.25),maxLineGap=max(1,int(width*0.02)))
    if detected is None:
        segments=[]
    else:
        detected=np.asarray(detected)
        if detected.ndim not in (2,3) or detected.shape[-1]!=4 or (detected.ndim==3 and detected.shape[1]!=1):
            raise ValueError('HOUGH_SHAPE')
        segments=[tuple(map(int,row)) for row in detected.reshape(-1,4)]
    return estimate_segments(segments,width,height)
