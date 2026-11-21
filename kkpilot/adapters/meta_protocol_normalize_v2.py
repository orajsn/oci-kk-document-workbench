"""Meta response representation repair; strict extraction validation remains required."""
import copy
import hashlib
import json

ROOT_KEYS = frozenset(('wire_version', 'layout_family', 'h', 'm'))
ECHOES = {'type':'object', 'additionalProperties':False}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')).hexdigest()


def normalize_and_expand(wire, schema, context, expand_fn, house_aliases, member_aliases):
    if not isinstance(wire,dict) or not ROOT_KEYS.issubset(wire): raise ValueError('INVALID_META_ROOT')
    extras=set(wire)-ROOT_KEYS
    if not extras.issubset(ECHOES): raise ValueError('UNEXPECTED_META_ROOT_KEY')
    for key in extras:
        v=ECHOES[key]
        if type(wire[key]) is not type(v) or wire[key]!=v or type(schema.get(key)) is not type(v) or schema[key]!=v: raise ValueError('INVALID_SCHEMA_ECHO')
    original={k:wire[k] for k in ROOT_KEYS}; before=_sha(original); clean=copy.deepcopy(original)
    audit=[]; originals=[]
    def fix(obj, aliases, prefix):
        if not isinstance(obj,dict): return
        for alias,field in aliases.items():
            if alias not in obj: continue
            value=obj[alias]
            if isinstance(value,str) and len(value)<=2000 and '\x00' not in value and not value.strip():
                audit.append({'path':prefix+field,'raw_kind':'EMPTY_STRING' if not value else 'WHITESPACE_STRING','raw_length':len(value)})
                originals.append((obj,alias,value));obj[alias]=None
    fix(clean.get('h'),house_aliases,'household.')
    if isinstance(clean.get('m'),list):
        for i,row in enumerate(clean['m']): fix(row,member_aliases,'members['+str(i)+'].')
    after=_sha(clean)
    verification=copy.deepcopy(clean)
    # Restore only the explicitly logged locations, and compare the complete canonical tree.
    for obj, alias, value in originals: obj[alias]=value
    if _sha(clean)!=before or clean!=original: raise ValueError('UNAUTHORIZED_CANONICAL_CHANGE')
    # Reapply only the listed representation changes to the verified tree.
    for obj,alias,_ in originals: obj[alias]=None
    if clean!=verification or _sha(clean)!=after or _sha(original)!=before: raise ValueError('UNAUTHORIZED_CANONICAL_CHANGE')
    extraction,provenance=expand_fn(clean,context)
    if _sha(clean)!=after or _sha(original)!=before: raise ValueError('CANONICAL_MUTATED_BY_EXPANSION')
    for entry in audit:
        extraction['review_flags'].append({'path':entry['path'],'code':'MODEL_EMPTY_OUTPUT','message':'The model returned an empty value; review the source image.'})
    return extraction,provenance,{'decoder':'meta-empty-representation-v2','ignored_schema_echoes':sorted(extras),'canonical_sha256_before':before,'canonical_sha256_after':after,'missingness_changes':audit,'only_listed_changes_verified':True}
