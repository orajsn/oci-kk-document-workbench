
let cases=[], csrf="";
const labels={confirm:'Sesuai cetakan',correct:'Dikoreksi',unreadable:'Masih tidak terbaca'};
const $=id=>document.getElementById(id);
let current=cases[0], openedVersion=null;
const decisions=new Map();
let busy=false, pendingRequest=null;
async function api(url,options={}){
 const response=await fetch(url,{cache:'no-store',...options});const result=await response.json();
 if(!response.ok){const e=new Error(result.error?.message||'Permintaan gagal.');e.status=response.status;throw e}return result
}
async function refresh(){const result=await api('/api/nik-review'+(new URLSearchParams(location.search).has('job')?'?job='+encodeURIComponent(new URLSearchParams(location.search).get('job')):''));cases=result.cases;decisions.clear();for(const c of cases)if(c.decision)decisions.set(c.key,c.decision)}
function node(tag,text){const e=document.createElement(tag);e.textContent=text;return e}
function inFilter(c){
 const d=decisions.get(c.key);
 switch($('filter').value){case 'open':return !d;case 'done':return !!d;case 'unreadable':return d?.action==='unreadable';default:return true}
}
function sidebar(){
 $('cohort').textContent=cases.length+' NIK · '+new Set(cases.map(c=>c.doc)).size+' KK';
 $('count').textContent=decisions.size+' dari '+cases.length+' sudah diperiksa';$('bar').max=cases.length;$('bar').value=decisions.size;
 const q=$('queue');q.replaceChildren();
 cases.filter(inFilter).forEach(c=>{const b=document.createElement('button');b.type='button';b.dataset.id=c.id;b.setAttribute('aria-current',String(c===current));const d=decisions.get(c.key);
 b.append(node('b',c.doc+' · Baris '+c.row),node('small',c.topic+' · '+(d?labels[d.action]:'Belum diperiksa')));b.onclick=()=>show(c);q.append(b)});
 if(!q.children.length)q.append(node('p','Tidak ada kasus pada filter ini.'));
}
function zoom(){const factor=Number($('zoom').value);$('photo').style.width=(current.width*factor)+'px';
 $('amount').textContent=factor.toLocaleString('id-ID')+'×';$('zoom').setAttribute('aria-valuetext',$('amount').textContent);$('native').hidden=factor===1;}
function show(c){
 current=c;sidebar();$('panel').hidden=!c;if(!c)return;
 $('title').textContent=c.doc+' · Anggota baris '+c.row;$('topic').textContent=c.topic;$('why').replaceChildren(...c.reasons.map(t=>node('li',t)));
 $('photo').src=c.image;$('small').src=c.image;$('small').width=c.width;$('ctximg').src=c.context;$('ctx').open=false;
 $('zoom').value=1;zoom();$('frame').scrollLeft=0;$('stored').textContent=c.stored;
 $('form').hidden=true;$('actions').hidden=false;$('error').textContent='';$('entry').removeAttribute('aria-invalid');
 const d=decisions.get(c.key);openedVersion=c.revision;$('last').hidden=!d;$('last').replaceChildren();
 if(d){$('last').append(node('strong',labels[d.action]),node('p',d.action==='unreadable'?'Perlu foto yang lebih jelas.':d.action==='correct'?'Nilai koreksi: '+d.value:'Nilai tersimpan sesuai cetakan.'));
 $('last').append(node('small','Tersimpan di Oracle · '+d.operator+' · '+new Date(d.time).toLocaleString('id-ID')));
 if(c.structural&&d.action!=='unreadable')$('last').append(node('small','Konfirmasi cetakan tercatat. Konsistensi data identitas perlu dicek terpisah.'))}
}
function chooseFiltered(){const list=cases.filter(inFilter);show(list.includes(current)?current:list[0]);}
function lock(on){
 busy=on;for(const id of ['confirm','correct','unclear','entry','cancel','filter'])$(id).disabled=on;
 $('form').querySelector('[type=submit]').disabled=on;for(const e of $('queue').querySelectorAll('button'))e.disabled=on;
}
async function persist(action,value){
 const c=current;if(!c||busy)return;const rev=openedVersion;const signature=JSON.stringify([c.key,rev,action,value??null]);
 if(!pendingRequest||pendingRequest.signature!==signature)pendingRequest={signature,id:crypto.randomUUID()};
 lock(true);$('notice').textContent='Menyimpan ke Oracle Database…';
 try{
  await api('/api/nik-review/'+c.key+'/decision',{method:'POST',headers:{'Content-Type':'application/json','X-KK-Pilot-Token':csrf},body:JSON.stringify({action,value:value??null,expected_revision:rev,request_id:pendingRequest.id})});
  await refresh();pendingRequest=null;
  const list=cases.filter(inFilter);show(list.find(x=>!decisions.has(x.key))??list.find(x=>x.key===c.key)??list[0]);
  $('notice').textContent=c.doc+' baris '+c.row+' · '+labels[action]+' tersimpan di Oracle.'+(decisions.size===cases.length?' Semua kasus sudah diperiksa.':'');if(current)$('title').focus();
 }catch(e){
  if(e.status===409){await refresh();show(cases.find(x=>x.key===c.key));pendingRequest=null}
  $('notice').textContent=e.status?e.message:'Status simpan belum terkonfirmasi. Coba kembali dengan keputusan yang sama.';
 }finally{lock(false)}
}
$('zoom').oninput=zoom;$('reset').onclick=()=>{$('zoom').value=1;zoom();$('frame').scrollLeft=0};
$('filter').onchange=chooseFiltered;$('confirm').onclick=()=>persist('confirm');$('unclear').onclick=()=>persist('unreadable');
$('correct').onclick=()=>{const old=decisions.get(current.key);$('entry').value=old?.action==='correct'?old.value:'';$('form').hidden=false;$('actions').hidden=true;$('error').textContent='';$('entry').focus()};
$('cancel').onclick=()=>{$('form').hidden=true;$('actions').hidden=false;$('correct').focus()};
$('entry').oninput=()=>{$('entry').removeAttribute('aria-invalid');$('error').textContent=''};
$('form').onsubmit=e=>{e.preventDefault();const v=$('entry').value.trim();
 const digit=s=>s.replace(/[ .-]/g,'');let error='';
 if(!/^[0-9]+(?:[ .-]+[0-9]+)*$/.test(v))error='Masukkan angka yang terbaca. Jika masih ragu, pilih “Masih tidak terbaca”.';
 else if(!current.legacy&&digit(v).length!==16)error='NIK ini memerlukan 16 digit. Periksa kembali atau tandai masih tidak terbaca.';
 else if(digit(v)===digit(current.stored))error='Angkanya sama dengan nilai tersimpan. Pilih konfirmasi jika sesuai cetakan.';
 if(error){$('error').textContent=error;$('entry').setAttribute('aria-invalid','true');$('entry').focus();return}persist('correct',v)};
async function init(){
 $('panel').hidden=true;
 try{const cfg=await api('/api/config');csrf=cfg.csrf_token;await refresh();show(cases.find(c=>!decisions.has(c.key))??cases[0])}
 catch(e){$('notice').textContent='Antrean belum bisa dibuka. Muat ulang untuk mencoba lagi.'}
}
init();
