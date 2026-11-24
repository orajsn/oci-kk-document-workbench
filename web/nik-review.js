
let cases=[], csrf="";
const labels={confirm:'Confirmed as printed',correct:'Corrected',unreadable:'Still unreadable'};
const $=id=>document.getElementById(id);
let current=cases[0], openedVersion=null;
const decisions=new Map();
let busy=false, pendingRequest=null;
async function api(url,options={}){
 const response=await fetch(url,{cache:'no-store',...options});const result=await response.json();
 if(!response.ok){const e=new Error(result.error?.message||'Request failed.');e.status=response.status;throw e}return result
}
async function refresh(){const result=await api('/api/nik-review'+(new URLSearchParams(location.search).has('job')?'?job='+encodeURIComponent(new URLSearchParams(location.search).get('job')):''));cases=result.cases;decisions.clear();for(const c of cases)if(c.decision)decisions.set(c.key,c.decision)}
function node(tag,text){const e=document.createElement(tag);e.textContent=text;return e}
// The API includes saved reason text from several pilot releases.
const legacyReasons = new Map([
  [
    "Nomor tersimpan tidak memenuhi format 16 digit. Periksa format yang tercetak.",
    "The saved number does not match the 16-digit format. Check the format printed on the card."
  ],
  [
    "Nomor yang sama muncul pada beberapa anggota.",
    "The same number appears on several members."
  ],
  [
    "Bagian tanggal atau kode jenis kelamin pada NIK berbeda dengan data anggota.",
    "The NIK date or sex code differs from the member data."
  ],
  [
    "Bacaan awal atau hybrid kosong atau berbeda dari nilai tersimpan.",
    "The initial or hybrid reading is missing or differs from the saved number."
  ],
  [
    "OCR belum menguatkan satu baris dengan seluruh digit yang sama.",
    "OCR did not confirm a unique row with all the same digits."
  ],
  [
    "Lokasi baris belum terverifikasi. Periksa gambar konteks.",
    "The row location is unverified. Check the context image."
  ],
  [
    "Pembacaan ulang belum dapat dikonfirmasi. Periksa crop.",
    "The reread could not be confirmed. Check the crop."
  ],
  [
    "Digit bacaan ulang cocok dengan hasil tersimpan.",
    "The reread matches the saved digits."
  ],
  [
    "Bacaan ulang berbeda atau mengandung digit yang tidak terbaca.",
    "The reread differs or contains unreadable digits."
  ],
  [
    "Gemini menandai crop sebagai ragu.",
    "The model marked the crop as uncertain."
  ],
  [
    "Bacaan baru berbeda dengan koreksi petugas pada foto dan baris yang sama. Cocokkan NIK dengan cetakan.",
    "The reading differs from an earlier correction for this image and row. Check the print."
  ],
  [
    "Bacaan ulang cocok dengan nomor tersimpan. Konflik data di atas masih perlu diperiksa.",
    "The reread matches the saved NIK. The data conflict still needs review."
  ],
  [
    "OCR awal belum menemukan satu baris yang cukup jelas dan cocok dengan seluruh nomor.",
    "Initial OCR could not confirm all digits on one clearly identified row."
  ],
  [
    "Saat membaca ulang crop, Gemini masih ragu meski digitnya sama.",
    "The crop reread returned the same digits with uncertainty."
  ],
  [
    "Bacaan ulang tidak menghasilkan urutan digit yang sama dengan nomor tersimpan.",
    "The reread did not return the same sequence of digits as the saved NIK."
  ]
]);
const reasonKeys = new Map([...legacyReasons].map(([old, current]) => [current, old]));
const partNames = {hari:'day',bulan:'month',tahun:'year','hari dan bulan':'day and month','bulan dan tahun':'month and year'};
function readableReason(value) {
  if (legacyReasons.has(value)) return legacyReasons.get(value);
  let m = /^Bagian (hari dan bulan|bulan dan tahun|bulan|tahun|hari) pada NIK tidak cocok dengan data kelahiran anggota yang tersimpan\.$/.exec(value);
  if (m) return 'The ' + partNames[m[1]] + ' in the NIK differs from the recorded birth date.';
  m = /^Bacaan ulang berbeda pada digit ke-([1-9]\d?) dari kiri\.$/.exec(value);
  if (m) return 'The reread differs at digit ' + m[1] + ' from the left.';
  m = /^Nomor tersimpan berisi (\d{1,2}) digit\./.exec(value);
  if (m) return 'The saved NIK contains ' + m[1] + ' digits. Check its printed length.';
  return value;
}
const reviewTopics = {"Lokasi belum pasti": "Row location uncertain", "Format NIK": "NIK format", "Konflik data": "Conflicting data", "Bacaan berbeda": "Readings differ", "Digit samar": "Unclear digit", "Konflik koreksi petugas": "Earlier correction differs"};
function nikReviewGuide(reasons) {
  const observations = Array.isArray(reasons) ? reasons.filter(s => typeof s === 'string' && s.trim()) : [];
  const keys = observations.map(text => reasonKeys.get(text) || text);
  const has = pattern => keys.some(text => pattern.test(text));
  const match = pattern => keys.map(text => text.match(pattern)).find(Boolean);
  const facts = [];
  const checks = [];
  const add = (fact, check) => { facts.push(fact); if (check) checks.push(check); };
  if (has(/^Lokasi baris belum terverifikasi/)) {
    add('The location of this NIK on the image is uncertain.', 'Open the full row. Match the name and row number before reading the NIK.');
  }
  if (has(/^Bacaan baru berbeda dengan koreksi petugas/)) {
    add('This reading differs from an earlier reviewer correction for the same image and row.',
        'Check the printed digits before saving a decision.');
  }
  if (has(/^Nomor yang sama muncul pada beberapa anggota/)) {
    add('The same NIK appears on more than one member.',
        'Open the full row and match the name with the NIK beside it.');
  }
  const birthPart = match(/^Bagian (hari dan bulan|bulan dan tahun|bulan|tahun|hari) pada NIK tidak cocok dengan data kelahiran anggota yang tersimpan\.$/);
  if (birthPart || has(/^Bagian tanggal (pada NIK berbeda dari tanggal lahir|atau kode jenis kelamin pada NIK berbeda)/)) {
    const field = birthPart ? partNames[birthPart[1]] : '';
    const why = field ? 'The ' + field + ' in the NIK differs from this member’s recorded birth date.' :
      has(/^Bagian tanggal atau kode jenis kelamin/) ? 'The birth-date or sex code in the NIK conflicts with this member’s data.' :
      'The date code in the NIK differs from the member’s recorded birth date.';
    add(why, 'Compare the printed NIK on this member’s row with the original reading. If the digits match, choose Confirm as printed.');
  }
  const length = match(/^Nomor tersimpan berisi (\d{1,2}) digit\./);
  if (length || has(/^Nomor tersimpan tidak memenuhi format 16 digit/)) {
    add(length ? 'The original reading contains ' + length[1] + ' digits.' : 'The original reading differs from the usual 16-digit NIK format.',
        'Count the printed digits. For a legacy card, keep its printed length and do not add zeros.');
  }
  const position = match(/^Bacaan ulang berbeda pada digit ke-([1-9]\d?) dari kiri\.$/);
  if (position) {
    add('The two readings differ at digit ' + position[1] + ' from the left.',
        'Count from the left on the image and compare that digit with the original reading.');
  } else if (has(/^Bacaan ulang berbeda pada satu digit\./)) {
    add('The two readings differ by one digit.', 'Compare the printed NIK with the original reading from left to right.');
  } else if (has(/^Bacaan ulang (berbeda atau mengandung digit yang tidak terbaca|tidak menghasilkan urutan digit yang sama)/)) {
    add('The reread could not confirm the same sequence of digits.',
        'Compare the digits from left to right. Choose Still unreadable if any digit is unclear.');
  }
  const uncertain = has(/^(Gemini menandai crop sebagai ragu|Saat membaca ulang crop, Gemini masih ragu)/);
  const same = has(/^(Digit bacaan ulang cocok|Bacaan ulang cocok dengan nomor tersimpan|Saat membaca ulang crop, Gemini masih ragu meski digitnya sama)/);
  if (uncertain) {
    add(same ? 'Both readings returned the same number, but the reread marked some digits as uncertain.' :
      'The reread marked some digits as uncertain.', 'Compare the original size with the zoomed view. If the digits remain unclear, choose Still unreadable.');
  } else if (same && facts.length) {
    facts.push('The reread matches the original number. The issue above still needs review.');
  }
  if (!facts.length) {
    if (has(/^OCR (awal belum menemukan satu baris|belum menguatkan satu baris)/)) {
      add('Image recognition did not identify one clear row with the complete matching number.',
          'Open the full row, check the member name and compare the NIK from left to right.');
    } else if (has(/^Bacaan awal atau hybrid kosong atau berbeda/)) {
      add('One of the initial readings was missing or differed from the saved NIK.',
          'Compare the original reading with the printed NIK.');
    } else if (has(/^Pembacaan ulang belum dapat dikonfirmasi/)) {
      add('No confirmed reread is available for this NIK.', 'Compare the number directly with the printed NIK.');
    } else {
      facts.push(...observations.map(readableReason));
    }
  }
  if (!facts.length) facts.push('No specific reason was recorded for this case.');
  return {facts, check:checks[0] || 'Match the name and NIK on this member’s row before choosing a decision.', observations:observations.map(readableReason)};
}
function displayNikGuide(c) {
  const guide = nikReviewGuide(c.reasons);
  $('why').replaceChildren(...guide.facts.map(text => node('li',text)));
  $('nik-check').textContent = guide.check;
  const extra = guide.observations.filter(text => !guide.facts.includes(text));
  $('nik-original-details').hidden = !extra.length;
  $('nik-original-details').open = false;
  $('nik-original-reasons').replaceChildren(...extra.map(text => node('li',text)));
}

function inFilter(c){
 const d=decisions.get(c.key);
 switch($('filter').value){case 'open':return !d;case 'done':return !!d;case 'unreadable':return d?.action==='unreadable';default:return true}
}
function sidebar(){
 $('cohort').textContent=cases.length+' NIKs · '+new Set(cases.map(c=>c.doc)).size+' cards';
 $('count').textContent=decisions.size+' of '+cases.length+' reviewed';$('bar').max=Math.max(1,cases.length);$('bar').value=decisions.size;
 const q=$('queue');q.replaceChildren();
 cases.filter(inFilter).forEach(c=>{const b=document.createElement('button');b.type='button';b.dataset.id=c.id;b.setAttribute('aria-current',String(c===current));const d=decisions.get(c.key);
 b.append(node('b',c.doc+' · Row '+c.row),node('small',(reviewTopics[c.topic] || c.topic)+' · '+(d?labels[d.action]:'Unreviewed')));b.onclick=()=>show(c);q.append(b)});
 if(!q.children.length)q.append(node('p','No cases match this filter.'));
}
function zoom(){const factor=Number($('zoom').value);$('photo').style.width=(current.width*factor)+'px';
 $('amount').textContent=factor.toLocaleString('en-US')+'×';$('zoom').setAttribute('aria-valuetext',$('amount').textContent);$('native').hidden=factor===1;}
function show(c){
 current=c;sidebar();$('panel').hidden=!c;$('empty-detail').hidden=!!c;if(!c)return;
 $('title').textContent=c.doc+' · Member row '+c.row;$('topic').textContent=reviewTopics[c.topic] || c.topic;displayNikGuide(c);
 $('photo').src=c.image;$('small').src=c.image;$('small').width=c.width;$('ctximg').src=c.context;$('ctx').open=false;
 $('zoom').value=1;zoom();$('frame').scrollLeft=0;$('stored').textContent=c.stored;
 $('form').hidden=true;$('actions').hidden=false;$('error').textContent='';$('entry').removeAttribute('aria-invalid');
 const d=decisions.get(c.key);openedVersion=c.revision;$('last').hidden=!d;$('last').replaceChildren();
 if(d){$('last').append(node('strong',labels[d.action]),node('p',d.action==='unreadable'?'A clearer photo is needed.':d.action==='correct'?'Corrected value: '+d.value:'The original reading matches the printed NIK.'));
 $('last').append(node('small','Recorded by '+d.operator+' · '+new Date(d.time).toLocaleString('en-US')));
 if(c.structural&&d.action!=='unreadable')$('last').append(node('small','This decision records what is printed. The member data discrepancy still needs investigation.'))}
}
function chooseFiltered(){const list=cases.filter(inFilter);show(list.includes(current)?current:list[0]);}
function lock(on){
 busy=on;for(const id of ['confirm','correct','unclear','entry','cancel','filter'])$(id).disabled=on;
 $('form').querySelector('[type=submit]').disabled=on;for(const e of $('queue').querySelectorAll('button'))e.disabled=on;
}
async function persist(action,value){
 const c=current;if(!c||busy)return;const rev=openedVersion;const signature=JSON.stringify([c.key,rev,action,value??null]);
 if(!pendingRequest||pendingRequest.signature!==signature)pendingRequest={signature,id:crypto.randomUUID()};
 lock(true);$('notice').textContent='Saving decision…';
 try{
  await api('/api/nik-review/'+c.key+'/decision',{method:'POST',headers:{'Content-Type':'application/json','X-KK-Pilot-Token':csrf},body:JSON.stringify({action,value:value??null,expected_revision:rev,request_id:pendingRequest.id})});
  await refresh();pendingRequest=null;
  const list=cases.filter(inFilter);show(list.find(x=>!decisions.has(x.key))??list.find(x=>x.key===c.key)??list[0]);
  $('notice').textContent='Decision for '+c.doc+' row '+c.row+' saved. '+labels[action]+'.'+(decisions.size===cases.length?' All cases have been reviewed.':'');if(current)$('title').focus();
 }catch(e){
  if(e.status===409){await refresh();show(cases.find(x=>x.key===c.key));pendingRequest=null}
  $('notice').textContent=e.status?e.message:'Save status is unconfirmed. Retry with the same decision.';
 }finally{lock(false)}
}
$('zoom').oninput=zoom;$('reset').onclick=()=>{$('zoom').value=1;zoom();$('frame').scrollLeft=0};
$('filter').onchange=chooseFiltered;$('confirm').onclick=()=>persist('confirm');$('unclear').onclick=()=>persist('unreadable');
$('correct').onclick=()=>{const old=decisions.get(current.key);$('entry').value=old?.action==='correct'?old.value:'';$('form').hidden=false;$('actions').hidden=true;$('error').textContent='';$('entry').focus()};
$('cancel').onclick=()=>{$('form').hidden=true;$('actions').hidden=false;$('correct').focus()};
$('entry').oninput=()=>{$('entry').removeAttribute('aria-invalid');$('error').textContent=''};
$('form').onsubmit=e=>{e.preventDefault();const v=$('entry').value.trim();
 const digit=s=>s.replace(/[ .-]/g,'');let error='';
 if(!/^[0-9]+(?:[ .-]+[0-9]+)*$/.test(v))error='Enter the digits you can read. If a digit remains unclear, choose Still unreadable.';
 else if(!current.legacy&&digit(v).length!==16)error='This NIK requires 16 digits. Check the print again or mark it as unreadable.';
 else if(digit(v)===digit(current.stored))error='These digits match the original reading. Choose Confirm as printed if they match the image.';
 if(error){$('error').textContent=error;$('entry').setAttribute('aria-invalid','true');$('entry').focus();return}persist('correct',v)};
async function init(){
 $('panel').hidden=true;
 try{const cfg=await api('/api/config');csrf=cfg.csrf_token;await refresh();show(cases.find(c=>!decisions.has(c.key))??cases[0])}
 catch(e){$('notice').textContent='Could not open the queue. Reload the page to try again.'}
}
init();
