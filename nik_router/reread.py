"""One stateless, image-only Gemini request per flagged job/version/row."""
import base64
import hashlib
import io
import json
import sqlite3
import time
import uuid

import oci
from PIL import Image
from kkpilot.adapters import oci_gemini as gem

PHASE = 'kk-live-bulk-nik-reread-v1'
PROMPT = ('Transcribe the printed identifier in this cropped cell exactly as visible, '
          'including leading zeros. Do not assume a length, complete a partial code, '
          'or infer characters. Use ? for indistinguishable digits. Return uncertain '
          'true whenever any character is unclear or clipped.')


class CropReader:
    def __init__(self, cfg, ledger):
        self.cfg = cfg
        self.chat = None
        self.db = sqlite3.connect(ledger, timeout=25)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS calls(
            phase TEXT, doc TEXT, stage TEXT, started REAL, ended REAL, outcome TEXT,
            request_id TEXT, provider_status INTEGER, PRIMARY KEY(phase,doc,stage))''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS nik_live_responses (
            job TEXT, stage TEXT, crop_sha TEXT NOT NULL, reading TEXT NOT NULL,
            uncertain INTEGER NOT NULL, seconds REAL NOT NULL, PRIMARY KEY(job,stage))''')
        self.db.commit()

    def close(self):
        self.db.close()

    def client(self):
        if self.chat is None:
            gem.check_approval(self.cfg)
            if gem.MODEL != 'google.gemini-2.5-pro':
                raise ValueError('unexpected_model')
            mgmt, chat = gem.OciGeminiExtractor(self.cfg)._get_clients(oci)
            profile = mgmt.get_routing_profile(gem.PROFILE,retry_strategy=oci.retry.NoneRetryStrategy())
            gem.verify_profile(oci.util.to_dict(profile.data),self.cfg)
            self.chat = chat
        return self.chat

    def read(self, job, version, row, image, source_sha):
        if source_sha not in self.cfg['approved_image_sha256s']:
            raise ValueError('unapproved_source')
        stage = f'v{version}-r{row}'
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        cached = self.db.execute('SELECT crop_sha,reading,uncertain,seconds FROM nik_live_responses WHERE job=? AND stage=?',(job,stage)).fetchone()
        if cached:
            if cached[0]!=digest: raise ValueError('crop_changed')
            return cached[1],bool(cached[2]),'returned',cached[3]
        prior = self.db.execute('SELECT outcome FROM calls WHERE phase=? AND doc=? AND stage=?',(PHASE,job,stage)).fetchone()
        if prior:
            # The provider may have received a request even if the process died.
            if prior[0]=='INTENT':
                with self.db:
                    self.db.execute("UPDATE calls SET outcome='UNKNOWN_OUTCOME',ended=? WHERE phase=? AND doc=? AND stage=? AND outcome='INTENT'",(time.time(),PHASE,job,stage))
            return '',True,'unresolved_call',0
        client = self.client()
        view = image.resize((image.width*2,image.height*2),Image.Resampling.BICUBIC)
        buf = io.BytesIO();view.save(buf,format='PNG')
        models = oci.generative_ai_inference.models
        image_input = models.ImageContent(image_url=models.ImageUrl(url='data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode(),detail='AUTO'))
        schema = {'type':'object','properties':{'reading':{'type':'string'},'uncertain':{'type':'boolean'}},
                  'required':['reading','uncertain'],'additionalProperties':False}
        packet = models.ChatDetails(compartment_id=gem.COMPARTMENT,serving_mode=models.OnDemandServingMode(model_id=gem.PROFILE),
            chat_request=models.GenericChatRequest(messages=[models.SystemMessage(content=[models.TextContent(text=PROMPT)]),
            models.UserMessage(content=[image_input])],temperature=0,max_tokens=8192,num_generations=1,is_stream=False,
            response_format=models.JsonSchemaResponseFormat(json_schema=models.ResponseJsonSchema(name='nik_read',schema=schema,is_strict=True))))
        rid='kk-nik-reread-'+uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO calls(phase,doc,stage,started,outcome,request_id) VALUES (?,?,?,?,?,?)',
                            (PHASE,job,stage,time.time(),'INTENT',rid))
        t=time.monotonic();status=None
        try:
            resp=client.chat(packet,retry_strategy=oci.retry.NoneRetryStrategy(),opc_request_id=rid)
            status=resp.status
            raw=oci.util.to_dict(resp.data);head=gem._header_metadata(resp.headers)
            if status!=200 or head.get('served_region')!=gem.SERVING or head.get('served_region_invalid') or raw.get('model_id') not in (gem.MODEL,gem.PROFILE):
                raise ValueError('provider_identity')
            choices=raw['chat_response']['choices']
            if len(choices)!=1 or str(choices[0]['finish_reason']).lower()!='stop':raise ValueError('incomplete_response')
            data,_=gem.contract.decode_json(''.join(x['text'] for x in choices[0]['message']['content'] if x['type']=='TEXT'))
            if type(data.get('reading')) is not str or type(data.get('uncertain')) is not bool or len(data['reading'])>120:raise ValueError('read_shape')
        except Exception as exc:
            status=status or getattr(exc,'status',None)
            with self.db:
                self.db.execute('UPDATE calls SET ended=?,outcome=?,provider_status=? WHERE phase=? AND doc=? AND stage=?',
                                (time.time(),'SERVICE_ERROR' if status else 'UNKNOWN_OUTCOME',status,PHASE,job,stage))
            return '',True,'provider_error',round(time.monotonic()-t,3)
        seconds=round(time.monotonic()-t,3)
        with self.db:
            self.db.execute('INSERT INTO nik_live_responses VALUES (?,?,?,?,?,?)',
                            (job,stage,digest,data['reading'],int(data['uncertain']),seconds))
            self.db.execute("UPDATE calls SET ended=?,outcome='RETURNED',provider_status=? WHERE phase=? AND doc=? AND stage=?",
                            (time.time(),status,PHASE,job,stage))
        return data['reading'],data['uncertain'],'returned',seconds
