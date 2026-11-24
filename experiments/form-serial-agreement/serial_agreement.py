"""Confirm a printed form serial before persistence using local, unprompted crops."""
import re
from split_serial import RESERVED,NUMBER
FIELD='household.form_serial'

def agreed(prefix,number,reads):
    if not isinstance(prefix,str) or not re.fullmatch(r'[A-Z]{1,4}',prefix,re.ASCII) or prefix in RESERVED:
        return False
    if not isinstance(number,str) or not NUMBER.fullmatch(number):
        return False
    if not isinstance(reads,dict) or set(reads)!={'label','series','number'}:
        return False
    def text(role):
        items=reads[role]
        if not isinstance(items,list) or len(items)!=2:return ()
        return tuple(r.get('text') if isinstance(r,dict) else None for r in items)
    return (all(isinstance(v,str) and re.fullmatch(r'NO(?:MOR)?[.,:]*',v,re.ASCII|re.I) for v in text('label'))
            and len(text('label'))==2 and text('series')==(prefix,prefix) and text('number')==(number,number))

def apply(saved,proposal,reads):
    if not proposal or any(f.get('path')==FIELD for f in saved.get('review_flags',())):
        return saved
    old=saved['household'].get('form_serial')
    prefix=proposal['prefix'];number=proposal['number']
    if old!=number or not agreed(prefix,number,reads):
        return saved
    return {**saved,'household':{**saved['household'],'form_serial':prefix+' '+number}}
