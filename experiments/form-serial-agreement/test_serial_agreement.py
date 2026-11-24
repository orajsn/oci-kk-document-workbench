from copy import deepcopy
import unittest
from split_serial import pair
from serial_agreement import apply

def box(l,t,r,b):
    return [[l,t],[r,t],[r,b],[l,b]]

class FormSerialTest(unittest.TestCase):
    def setUp(self):
        self.doc={'household':{'form_serial':'003456','kk_number_raw':None},'members':[],'review_flags':[]}
        self.frame=(400,300)
        self.ocr={'rec_texts':['No. QX','003456'],'rec_scores':[.94,.99],'rec_polys':[box(30,12,77,31),box(70,10,151,34)]}
        self.reads={k:[{'text':v},{'text':v}] for k,v in [('label','No.'),('series','QX'),('number','003456')]}

    def candidate(self):
        return pair(self.doc,self.doc,self.doc,self.ocr,self.frame)

    def test_leading_zeroes_independent_fields_and_idempotency(self):
        p=self.candidate()
        result=apply(self.doc,p,self.reads)
        self.assertEqual(result['household']['form_serial'],'QX 003456')
        self.assertEqual(self.doc['household']['form_serial'],'003456')
        self.assertEqual(result['members'],self.doc['members'])
        self.assertIs(apply(result,p,self.reads),result)

    def test_refuses_mismatches_in_any_crop(self):
        p=self.candidate()
        for role,other in [('label','NIK'),('series','QY'),('series','qX'),('series','Q8'),('number','003457'),('number','3456')]:
            with self.subTest(role=role,other=other):
                r=deepcopy(self.reads);r[role][1]['text']=other
                self.assertIs(apply(self.doc,p,r),self.doc)

    def test_wrong_consensus_disagrees_with_page(self):
        for role,v in [('series','QY'),('number','003457')]:
            r=deepcopy(self.reads);r[role]=[{'text':v}]*2
            self.assertIs(apply(self.doc,self.candidate(),r),self.doc)

    def test_missing_or_duplicated_inputs(self):
        for role in ('label','series','number'):
            for length in (0,1,3):
                r=deepcopy(self.reads);r[role]=[self.reads[role][0]]*length
                self.assertIs(apply(self.doc,self.candidate(),r),self.doc)

    def test_existing_field_review_retained(self):
        self.doc['review_flags']=[{'path':'household.form_serial','code':'EXISTING','message':'Check source.'}]
        self.assertIs(apply(self.doc,self.candidate(),self.reads),self.doc)

    def test_source_and_extraction_disagreements(self):
        alt=deepcopy(self.doc);alt['household']['form_serial']='003457'
        self.assertIsNone(pair(self.doc,alt,self.doc,self.ocr,self.frame))
        self.assertIsNone(pair(self.doc,self.doc,alt,self.ocr,self.frame))

    def test_collisions_and_reserved_labels(self):
        self.doc['members']=[{'nik_raw':'003456'}]
        self.assertIsNone(self.candidate())
        self.doc['members']=[]
        for v in ('No. KK','No. NIP','No. Q8','No. Q?'):
            self.ocr['rec_texts'][0]=v
            self.assertIsNone(self.candidate())

    def test_missing_or_competing_geometry(self):
        for label,poly in [('003456',box(172,12,219,34)),('523456',box(78,12,140,34)),('No. QY',box(30,12,76,31))]:
            o=deepcopy(self.ocr);o['rec_texts'].append(label);o['rec_polys'].append(poly);o['rec_scores'].append(.94)
            self.assertIsNone(pair(self.doc,self.doc,self.doc,o,self.frame))
        self.ocr['rec_polys'][0]=box(30,52,77,75)
        self.assertIsNone(self.candidate())

    def test_low_page_confidence(self):
        for pos in (0,1):
            o=deepcopy(self.ocr);o['rec_scores'][pos]=.5
            self.assertIsNone(pair(self.doc,self.doc,self.doc,o,self.frame))

    def test_two_scales_share_the_same_source(self):
        # If the whole-page reading and every crop make the same mistake, the gate lacks contrary evidence.
        self.ocr['rec_texts'][0]='No. QY';self.reads['series']=[{'text':'QY'}]*2
        self.assertEqual(apply(self.doc,self.candidate(),self.reads)['household']['form_serial'],'QY 003456')

if __name__=='__main__':unittest.main()
