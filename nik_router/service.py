"""Post-commit NIK review for newly uploaded KK jobs. One local consumer."""
import fcntl
import hashlib
import io
import json
import os
import re
from collections import Counter
from pathlib import Path
from uuid import UUID
from urllib.request import build_opener, ProxyHandler

import oracledb
from PIL import Image,ImageDraw,ImageOps
from kkpilot.adapters import oci_gemini as gem
from kkpilot.cloud.clients import database_factory
from kkpilot.cloud.config import load_config,validate_config
from routing import classify
from reread import CropReader
from locator import find_rows
import legacy_print

BASE=Path(__file__).resolve().parent
ROOT=Path('/var/lib/kk-pilot')
DIAG=ROOT/'diagnostics'
STATE=ROOT/'bulk-nik-router'
def read(p):return json.loads(p.read_bytes())
def sha(b):return hashlib.sha256(b).hexdigest()
def lob(v):return v.read() if hasattr(v,'read') else v
def sequence(v):
    if isinstance(v,str) and re.fullmatch(r'[0-9]+(?:[ .-]+[0-9]+)*',v.strip()):return re.sub('[ .-]','',v.strip())
    return None
def as_png(im):
    f=io.BytesIO();im.save(f,format='PNG');return f.getvalue()


def signals(job,source):
    metas=[p for p in DIAG.glob(job+'.*.metadata.json') if len(p.name.split('.'))==4]
    empty={'members':[]}
    if len(metas)!=1:return empty,empty,{},None,None
    attempt=str(UUID(metas[0].name.split('.')[1]))
    meta=read(metas[0])['metadata']
    if meta['source_sha256']!=source:raise ValueError('diagnostic_source_changed')
    def optional(suffix):
        path=DIAG/(job+'.'+attempt+suffix)
        return read(path) if path.is_file() else {}
    def extract(suffix):
        doc=optional(suffix)
        if not doc:return empty
        wire,_=gem.contract.extract_wire(doc['response'])
        return gem.contract.expand_candidate(wire)['extraction']
    align=optional('.hybrid.evidence.json').get('row_alignment') or {}
    return (extract('.response.json'),extract('.hybrid.response.json'),optional('.ocr.raw.json').get('ocr',{}),
            align.get('angle_degrees') if align.get('status')=='AVAILABLE' else None,meta.get('sent_image_sha256'))


def prepare_image(data,profile,members):
    with Image.open(io.BytesIO(data)) as img:im=ImageOps.exif_transpose(img).convert('RGB')
    if not profile:return im,None
    transform=profile['transform']
    if transform['crop']:im=im.crop(transform['crop'])
    if transform['rotate']:im=im.rotate(transform['rotate'],expand=True)
    bounds=profile['boxes']
    if sha(im.tobytes())!=profile['canonical_sha'] or sorted(m['row_number'] for m in members)!=list(range(1,len(bounds)+1)):
        return im,None
    return im,profile


def tiles(im,profile,n):
    if not profile:
        hint=Image.new('RGB',(240,34),'white')
        ImageDraw.Draw(hint).text((4,8),'Check the member row in the context image.',fill='black')
        ctx=im.copy();ctx.thumbnail((1600,1600))
        return hint,ctx
    x,y,xx,yy=profile['boxes'][n-1];col=im.crop(profile['column'])
    if not 0<=x<xx<=col.width or not 0<=y<yy<=col.height:raise ValueError('invalid_crop')
    tile=col.crop((x,y,xx,yy))
    ident=im.crop(profile['names'])
    strip=ident.crop((0,y,ident.width,yy))
    ctx=Image.new('RGB',(strip.width+12+tile.width,tile.height),'white')
    ctx.paste(strip,(0,0));ctx.paste(tile,(strip.width+12,0))
    return tile,ctx


def reasons(flags,state,same,uncertain):
    orig=flags['reasons'];messages=[]
    if 'invalid_format' in orig:messages.append('The saved number does not match the 16-digit format. Check the format printed on the card.')
    if 'duplicate' in orig:messages.append('The same number appears on several members.')
    if 'birth_conflict' in orig:messages.append('The NIK date or sex code differs from the member data.')
    if 'model_missing_or_disagrees' in orig:messages.append('The initial or hybrid reading is missing or differs from the saved number.')
    if 'no_unique_clear_ocr' in orig:messages.append('OCR did not confirm a unique row with all the same digits.')
    if state=='unlocated':messages.append('The row location is unverified. Check the context image.')
    elif state!='returned':messages.append('The reread could not be confirmed. Check the crop.')
    else:
        messages.append('The reread matches the saved digits.' if same else 'The reread differs or contains unreadable digits.')
        if uncertain:messages.append('The model marked the crop as uncertain.')
    return messages


def process(connect,reader,profiles,cfg,job,version,digest,payload):
    if digest not in cfg['oci_adapter']['approved_image_sha256s']:raise ValueError('source_out_of_scope')
    primary,hybrid,ocr,angle,frame_hash=signals(job,digest)
    flags=classify(payload,primary,hybrid,ocr,angle)
    selected=[f for f in flags if f['primary']]
    held=[];audit=[];profile=profiles.get(digest)
    if selected:
        api=build_opener(ProxyHandler({}))
        with api.open('http://127.0.0.1:8765/api/jobs/'+job+'/image',timeout=25) as res:
            data=res.read(cfg['max_upload_bytes']+1)
        if sha(data)!=digest or len(data)>cfg['max_upload_bytes']:raise ValueError('source_image_mismatch')
        im,geometry=prepare_image(data,profile,payload['members'])
        generic={}
        if geometry is None and frame_hash and ocr:
            adapter=cfg['oci_adapter']
            try:
                frame=gem._normalized(data,adapter.get('preprocessing_by_sha256',{}).get(digest,{}),adapter['max_upload_bytes'])
                if sha(frame)==frame_hash:
                    with Image.open(io.BytesIO(frame)) as src:
                        generic=find_rows(src.convert('RGB'),ocr,payload['members'],angle)
            except (ValueError, OSError, gem.AdapterError):
                generic={}
        members={m['row_number']:m for m in payload['members']}
        eligible=legacy_print.eligible_numbers(payload)
        for f in selected:
            n=f['row'];original=members[n].get('nik_raw')
            tile,ctx=generic[n] if n in generic else tiles(im,geometry,n)
            value,uncertain,state,seconds=reader.read(job,version,n,tile,digest) if geometry or n in generic else ('',True,'unlocated',0)
            same=sequence(original) is not None and sequence(original)==sequence(value)
            legacy_confirmed=legacy_print.confirmed(eligible,f['reasons'],original,value,state,uncertain)
            clear=legacy_confirmed or (state=='returned' and same and not uncertain and not f['structural'])
            audit.append(dict(row=n,clear=clear,read_state=state,uncertain=uncertain,
                              matches_stored=same,structural=f['structural'],seconds=seconds,
                              legacy_print_confirmed=legacy_confirmed,
                              locator='verified_profile' if geometry else 'ocr_row' if n in generic else 'unlocated'))
            if clear:continue
            legacy='invalid_format' in f['reasons']
            topic=('Row location uncertain' if state=='unlocated' else 'NIK format' if legacy else 'Conflicting data' if f['structural']
                   else 'Readings differ' if not same else 'Unclear digit')
            held.append(dict(k=sha(f'auto-nik-v1:{job}:{version}:{n}'.encode())[:24],jid=job,ver=version,
                             src=digest,alias=((profile['alias'] if profile else 'KK')+'-'+job.replace('-','')[:6].upper()),
                             n=n,nik=original or '(empty)',topic=topic,flags=json.dumps(reasons(f,state,same,uncertain),ensure_ascii=False),
                             structural=int(f['structural']),legacy=int(legacy),width=tile.width,crop=as_png(tile),ctx=as_png(ctx)))
    # Result receipt and cases become visible together. No MERGE or UPDATE of existing reviews.
    cn=connect()
    try:
        with cn.cursor() as c:
            c.execute('SELECT COUNT(*) FROM KK_OWNER.KK_NIK_ROUTE_RUNS WHERE job_id=:jid',jid=job)
            if c.fetchone()[0]:return
            q=sum(x['read_state']!='unlocated' for x in audit)
            c.setinputsizes(report=oracledb.DB_TYPE_CLOB)
            c.execute("""INSERT INTO KK_OWNER.KK_NIK_ROUTE_RUNS
                (job_id,job_version,member_count,flagged,cleared,review_count,crop_reads,report_json)
                VALUES (:jid,:ver,:members,:flagged,:cleared,:held,:reads,:report)""",
                      jid=job,ver=version,members=len(flags),flagged=len(selected),cleared=len(selected)-len(held),
                      held=len(held),reads=q,report=json.dumps(audit))
            for data in held:
                c.setinputsizes(crop=oracledb.DB_TYPE_BLOB,ctx=oracledb.DB_TYPE_BLOB)
                c.execute("""INSERT INTO KK_OWNER.KK_NIK_REVIEW_CASES
                (field_key,job_id,original_job_version,source_hash,sample_alias,member_row,ai_nik,topic,flags_json,structural,legacy,image_width,crop_png,context_png)
                VALUES (:k,:jid,:ver,:src,:alias,:n,:nik,:topic,:flags,:structural,:legacy,:width,:crop,:ctx)""",data)
        cn.commit()
    finally:cn.close()


def main():
    os.umask(0o077);lock=(STATE/'worker.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    cfg=validate_config(load_config(Path('/etc/kk-pilot/cloud.json')))
    connect=database_factory(cfg,oracledb);profiles=read(BASE/'crop-profiles.json')
    cn=connect()
    try:
        with cn.cursor() as c:
            c.execute("""SELECT j.job_id,min(e.job_version),d.sha256 FROM KK_OWNER.KK_JOBS j
            JOIN KK_OWNER.KK_DOCUMENTS d ON d.document_id=j.document_id AND d.case_id=j.case_id
            JOIN KK_OWNER.KK_NIK_ROUTE_CONTROL b ON b.scope_id=j.cloud_scope_id
            JOIN KK_OWNER.KK_EXTRACTIONS e ON e.job_id=j.job_id AND e.source_kind='MODEL'
            WHERE j.cloud_scope_id=:scope AND j.processing_mode='oci' AND j.created_at>=b.enabled_from
              AND NOT EXISTS(SELECT 1 FROM KK_OWNER.KK_NIK_ROUTE_RUNS s WHERE s.job_id=j.job_id)
            GROUP BY j.job_id,d.sha256 ORDER BY MIN(j.created_at)""",scope=cfg['scope_id'])
            jobs=c.fetchall()
    finally:cn.close()
    if not jobs:return
    reader=CropReader(cfg['oci_adapter'],STATE/'calls.sqlite')
    try:
        for job,version,digest in jobs:
            try:
                job=str(UUID(job));cn=connect()
                try:
                    with cn.cursor() as c:
                        c.execute("SELECT payload_json FROM KK_OWNER.KK_EXTRACTIONS WHERE job_id=:j AND job_version=:v AND source_kind='MODEL'",j=job,v=version)
                        raw=lob(c.fetchone()[0]);payload=raw if isinstance(raw,dict) else json.loads(raw)
                finally:cn.close()
                process(connect,reader,profiles,cfg,job,version,digest.strip(),payload)
            except Exception as exc:
                # Log an opaque job ID and error class only; next poll reconciles any paid request.
                print('routing_pending',job,type(exc).__name__,getattr(exc.args[0],'code','') if exc.args else '',flush=True)
    finally:reader.close()

if __name__=='__main__':main()
