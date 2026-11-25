"""Oracle-backed field decisions; extraction records are read-only here."""
import json
import re
from uuid import UUID

from ..repository import DomainError
from .review_repository import _timestamp


CASES = "KK_OWNER.KK_NIK_REVIEW_CASES"
EVENTS = "KK_OWNER.KK_NIK_REVIEW_EVENTS"
FROM = f" FROM {CASES} c JOIN KK_OWNER.KK_JOBS j ON j.job_id=c.job_id "
LATEST = f""" LEFT JOIN {EVENTS} e ON e.field_key=c.field_key AND e.revision=(
    SELECT MAX(prev_evt.revision) FROM {EVENTS} prev_evt WHERE prev_evt.field_key=c.field_key) """


def invalid(message):
    return DomainError("INVALID_REVIEW", message, 400)


def listing(repo, job_id=None):
    restriction = ' AND c.job_id=:filter_job' if job_id is not None else ''
    binds=dict(repo._scope_binds)
    if job_id is not None:binds['filter_job']=job_id
    sql = """SELECT c.field_key,c.sample_alias,c.member_row,c.ai_nik,c.topic,c.flags_json,
        c.image_width,c.structural,c.legacy,e.revision,e.action,e.corrected_nik,e.operator_name,
        SYS_EXTRACT_UTC(e.decided_at), c.job_id, j.case_id,
        (SELECT CASE WHEN COUNT(*)=1 THEN MAX(DBMS_LOB.SUBSTR(m.full_name,256,1)) END
         FROM KK_OWNER.KK_EXTRACTIONS x JOIN KK_OWNER.KK_MEMBERS m ON m.extraction_id=x.extraction_id
         WHERE x.job_id=c.job_id AND x.job_version=c.original_job_version AND m.row_number=c.member_row)""" + FROM + LATEST + "WHERE " + repo._scope + restriction + " ORDER BY c.sample_alias,c.member_row"
    with repo._session() as con, con.cursor() as cur:
        cur.execute(sql, binds)
        items = []
        for key,sid,row,nik,topic,flags,width,struct,legacy,rev,action,value,operator,when,job,reference,member_name in cur:
            decision = None if rev is None else dict(action=action,value=value,operator=operator,time=_timestamp(when))
            url = "/api/nik-review/" + key
            items.append(dict(key=key,job=job,reference=reference,member_name=member_name,id=f"{sid}-r{row}",doc=sid,row=row,stored=nik,topic=topic,
                              reasons=json.loads(flags),width=width,structural=bool(struct),legacy=bool(legacy),
                              image=url+"/crop",context=url+"/context",revision=rev or 0,decision=decision))
    return items


def image(repo, key, kind):
    column = {"crop":"crop_png","context":"context_png"}[kind]
    with repo._session() as con, con.cursor() as cur:
        cur.execute("SELECT c."+column+FROM+"WHERE "+repo._scope+" AND c.field_key=:fk",dict(repo._scope_binds,fk=key))
        row = cur.fetchone()
        if not row:
            raise DomainError("NOT_FOUND","Case not found.",404)
        value=row[0]
        return value.read() if hasattr(value,"read") else bytes(value)


def decide(repo, key, body):
    if not isinstance(body,dict) or set(body)!={"action","value","expected_revision","request_id"}:
        raise invalid("Invalid review request.")
    action,value,rev,rid = (body[k] for k in ("action","value","expected_revision","request_id"))
    if action not in ("confirm","correct","unreadable") or type(rev) is not int or not 0<=rev<1_000_000_000:
        raise invalid("Invalid decision or revision.")
    try:
        if str(UUID(rid))!=rid:raise ValueError
    except (TypeError,ValueError,AttributeError):
        raise invalid("Invalid request ID.") from None
    if action=="correct":
        if not isinstance(value,str) or len(value)>60 or not re.fullmatch(r"[0-9]+(?:[ .-]+[0-9]+)*",value):
            raise invalid("Enter the digits printed on the image.")
    elif value is not None:
        raise invalid("A corrected value is allowed only for a correction.")
    conflict=DomainError("VERSION_CONFLICT","The decision changed. Reload the case before saving.",409)
    with repo._session(write=True) as con, con.cursor() as cur:
        cur.execute("SELECT c.ai_nik,c.legacy"+FROM+"WHERE "+repo._scope+" AND c.field_key=:fk",dict(repo._scope_binds,fk=key))
        case=cur.fetchone()
        if not case:raise DomainError("NOT_FOUND","Case not found.",404)
        if action=="correct":
            digits=re.sub(r"[ .-]","",value)
            if (not case[1] and len(digits)!=16) or digits==re.sub(r"[ .-]","",case[0]):
                raise invalid("Check the NIK length, or confirm if the number is unchanged.")
        def previous():
            cur.execute(f"SELECT revision,action,corrected_nik,operator_name FROM {EVENTS} WHERE field_key=:fk AND request_id=:rid",fk=key,rid=rid)
            old=cur.fetchone()
            if old and tuple(old)!=(rev+1,action,value,repo.reviewer_identity):raise conflict
            return old
        if not previous():
            cur.execute(f"SELECT NVL(MAX(revision),0) FROM {EVENTS} WHERE field_key=:fk",fk=key)
            if cur.fetchone()[0]!=rev:raise conflict
            try:
                cur.execute(f"""INSERT INTO {EVENTS}
                    (field_key,revision,request_id,action,corrected_nik,operator_name)
                    VALUES (:fk,:revision,:rid,:action,:val,:actor)""",
                    fk=key,revision=rev+1,rid=rid,action=action,val=value,actor=repo.reviewer_identity)
            except repo.driver.IntegrityError as exc:
                if getattr(exc.args[0],"code",None)!=1:raise
                if not previous():raise conflict from None
    return {"revision":rev+1}

def routing_status(repo, job_id):
    sql = '''SELECT j.status, CASE WHEN b.enabled_from IS NOT NULL AND j.created_at>=b.enabled_from THEN 1 ELSE 0 END,
             r.member_count,r.flagged,r.cleared,r.review_count,r.crop_reads,
             (SELECT COUNT(*) FROM KK_OWNER.KK_NIK_REVIEW_CASES c WHERE c.job_id=j.job_id)
             FROM KK_OWNER.KK_JOBS j LEFT JOIN KK_OWNER.KK_NIK_ROUTE_CONTROL b ON b.scope_id=j.cloud_scope_id
             LEFT JOIN KK_OWNER.KK_NIK_ROUTE_RUNS r ON r.job_id=j.job_id
             WHERE ''' + repo._scope + ' AND j.job_id=:job_key'
    with repo._session() as con, con.cursor() as cur:
        cur.execute(sql,dict(repo._scope_binds,job_key=job_id))
        row=cur.fetchone()
        if row is None:raise DomainError('NOT_FOUND','Upload not found.',404)
    status,enrolled,members,flagged,cleared,held,reads,existing=row
    if held is not None:
        return dict(stage='complete',members=members,flagged=flagged,cleared=cleared,review=held,reads=reads)
    if not enrolled:return dict(stage='previous',review=existing)
    return dict(stage='failed' if status=='FAILED' else 'extracting' if status in ('QUEUED','PROCESSING') else 'checking',review=existing)
