
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
// The API includes saved reason text from several pilot releases.
function nikReviewGuide(reasons) {
  const observations = Array.isArray(reasons) ? reasons.filter(s => typeof s === 'string' && s.trim()) : [];
  const has = pattern => observations.some(text => pattern.test(text));
  const match = pattern => observations.map(text => text.match(pattern)).find(Boolean);
  const facts = [];
  const checks = [];
  const add = (fact, check) => { facts.push(fact); if (check) checks.push(check); };
  if (has(/^Lokasi baris belum terverifikasi/)) {
    add('Posisi NIK pada gambar belum bisa dipastikan.', 'Buka seluruh baris. Cocokkan nama dan nomor baris sebelum membaca NIK.');
  }
  if (has(/^Bacaan baru berbeda dengan koreksi petugas/)) {
    add('Hasil baca kali ini berbeda dari koreksi petugas sebelumnya untuk foto dan baris yang sama.',
        'Periksa lagi angka yang tercetak sebelum menyimpan keputusan.');
  }
  if (has(/^Nomor yang sama muncul pada beberapa anggota/)) {
    add('NIK yang sama terbaca pada lebih dari satu anggota.',
        'Buka seluruh baris dan cocokkan nama dengan NIK di sebelahnya.');
  }
  const birthPart = match(/^Bagian (hari dan bulan|bulan dan tahun|bulan|tahun|hari) pada NIK tidak cocok dengan data kelahiran anggota yang tersimpan\.$/);
  if (birthPart || has(/^Bagian tanggal (pada NIK berbeda dari tanggal lahir|atau kode jenis kelamin pada NIK berbeda)/)) {
    const field = birthPart ? birthPart[1] : '';
    const why = field ? 'Bagian ' + field + ' pada NIK berbeda dari tanggal lahir anggota ini.' :
      has(/^Bagian tanggal atau kode jenis kelamin/) ? 'Kode tanggal lahir atau jenis kelamin pada NIK berbeda dari data anggota ini.' :
      'Kode tanggal pada NIK berbeda dari tanggal lahir anggota ini.';
    add(why, 'Cocokkan NIK dengan cetakan pada baris anggota ini. Jika digitnya memang sama dengan hasil awal, pilih “Konfirmasi sesuai cetakan”.');
  }
  const length = match(/^Nomor tersimpan berisi (\d{1,2}) digit\./);
  if (length || has(/^Nomor tersimpan tidak memenuhi format 16 digit/)) {
    add(length ? 'Hasil awal berisi ' + length[1] + ' digit.' : 'Format hasil awal berbeda dari NIK 16 digit.',
        'Hitung digit pada cetakan. Untuk KK format lama, salin panjang aslinya tanpa menambah nol.');
  }
  const position = match(/^Bacaan ulang berbeda pada digit ke-([1-9]\d?) dari kiri\.$/);
  if (position) {
    add('Dua pembacaan berbeda pada digit ke-' + position[1] + ' dari kiri.',
        'Hitung dari kiri pada gambar, lalu bandingkan digit itu dengan hasil awal.');
  } else if (has(/^Bacaan ulang berbeda pada satu digit\./)) {
    add('Dua pembacaan berbeda pada satu digit.', 'Bandingkan NIK pada gambar dengan hasil awal dari kiri ke kanan.');
  } else if (has(/^Bacaan ulang (berbeda atau mengandung digit yang tidak terbaca|tidak menghasilkan urutan digit yang sama)/)) {
    add('Bacaan ulang belum memastikan deretan angka yang sama dengan hasil awal.',
        'Bandingkan digit dari kiri ke kanan. Pilih “Masih tidak terbaca” jika ada angka yang belum jelas.');
  }
  const uncertain = has(/^(Gemini menandai crop sebagai ragu|Saat membaca ulang crop, Gemini masih ragu)/);
  const same = has(/^(Digit bacaan ulang cocok|Bacaan ulang cocok dengan nomor tersimpan|Saat membaca ulang crop, Gemini masih ragu meski digitnya sama)/);
  if (uncertain) {
    add(same ? 'Kedua bacaan menghasilkan angka yang sama, tetapi ada digit yang masih diragukan saat baca ulang.' :
      'Bacaan ulang masih meragukan sebagian digit.', 'Coba ukuran asli dan zoom. Pilih “Masih tidak terbaca” jika digitnya tetap samar.');
  } else if (same && facts.length) {
    facts.push('Bacaan ulang cocok dengan hasil awal. Alasan di atas masih perlu diperiksa.');
  }
  if (!facts.length) {
    if (has(/^OCR (awal belum menemukan satu baris|belum menguatkan satu baris)/)) {
      add('Pembacaan gambar belum menemukan satu baris yang jelas dengan seluruh digit yang sama.',
          'Buka seluruh baris, cocokkan nama anggota, lalu bandingkan NIK dari kiri ke kanan.');
    } else if (has(/^Bacaan awal atau hybrid kosong atau berbeda/)) {
      add('Salah satu pembacaan awal kosong atau berbeda dari NIK yang tersimpan.',
          'Bandingkan digit hasil awal dengan NIK yang tercetak.');
    } else if (has(/^Pembacaan ulang belum dapat dikonfirmasi/)) {
      add('Hasil baca ulang belum tersedia untuk memastikan NIK.', 'Cocokkan langsung dengan NIK pada gambar.');
    } else {
      facts.push(...observations);
    }
  }
  if (!facts.length) facts.push('Alasan khusus untuk kasus ini belum tercatat.');
  return {facts, check:checks[0] || 'Cocokkan nama dan NIK pada baris anggota ini sebelum memilih keputusan.', observations};
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
 $('cohort').textContent=cases.length+' NIK · '+new Set(cases.map(c=>c.doc)).size+' KK';
 $('count').textContent=decisions.size+' dari '+cases.length+' sudah diperiksa';$('bar').max=Math.max(1,cases.length);$('bar').value=decisions.size;
 const q=$('queue');q.replaceChildren();
 cases.filter(inFilter).forEach(c=>{const b=document.createElement('button');b.type='button';b.dataset.id=c.id;b.setAttribute('aria-current',String(c===current));const d=decisions.get(c.key);
 b.append(node('b',c.doc+' · Baris '+c.row),node('small',c.topic+' · '+(d?labels[d.action]:'Belum diperiksa')));b.onclick=()=>show(c);q.append(b)});
 if(!q.children.length)q.append(node('p','Tidak ada kasus pada filter ini.'));
}
function zoom(){const factor=Number($('zoom').value);$('photo').style.width=(current.width*factor)+'px';
 $('amount').textContent=factor.toLocaleString('id-ID')+'×';$('zoom').setAttribute('aria-valuetext',$('amount').textContent);$('native').hidden=factor===1;}
function show(c){
 current=c;sidebar();$('panel').hidden=!c;$('empty-detail').hidden=!!c;if(!c)return;
 $('title').textContent=c.doc+' · Anggota baris '+c.row;$('topic').textContent=c.topic;displayNikGuide(c);
 $('photo').src=c.image;$('small').src=c.image;$('small').width=c.width;$('ctximg').src=c.context;$('ctx').open=false;
 $('zoom').value=1;zoom();$('frame').scrollLeft=0;$('stored').textContent=c.stored;
 $('form').hidden=true;$('actions').hidden=false;$('error').textContent='';$('entry').removeAttribute('aria-invalid');
 const d=decisions.get(c.key);openedVersion=c.revision;$('last').hidden=!d;$('last').replaceChildren();
 if(d){$('last').append(node('strong',labels[d.action]),node('p',d.action==='unreadable'?'Perlu foto yang lebih jelas.':d.action==='correct'?'Nilai koreksi: '+d.value:'NIK hasil baca awal sudah dicocokkan dengan cetakan.'));
 $('last').append(node('small','Dicatat oleh '+d.operator+' · '+new Date(d.time).toLocaleString('id-ID')));
 if(c.structural&&d.action!=='unreadable')$('last').append(node('small','Keputusan ini mencatat angka pada cetakan. Perbedaan pada data anggota tetap perlu ditelusuri.'))}
}
function chooseFiltered(){const list=cases.filter(inFilter);show(list.includes(current)?current:list[0]);}
function lock(on){
 busy=on;for(const id of ['confirm','correct','unclear','entry','cancel','filter'])$(id).disabled=on;
 $('form').querySelector('[type=submit]').disabled=on;for(const e of $('queue').querySelectorAll('button'))e.disabled=on;
}
async function persist(action,value){
 const c=current;if(!c||busy)return;const rev=openedVersion;const signature=JSON.stringify([c.key,rev,action,value??null]);
 if(!pendingRequest||pendingRequest.signature!==signature)pendingRequest={signature,id:crypto.randomUUID()};
 lock(true);$('notice').textContent='Menyimpan keputusan…';
 try{
  await api('/api/nik-review/'+c.key+'/decision',{method:'POST',headers:{'Content-Type':'application/json','X-KK-Pilot-Token':csrf},body:JSON.stringify({action,value:value??null,expected_revision:rev,request_id:pendingRequest.id})});
  await refresh();pendingRequest=null;
  const list=cases.filter(inFilter);show(list.find(x=>!decisions.has(x.key))??list.find(x=>x.key===c.key)??list[0]);
  $('notice').textContent='Keputusan untuk '+c.doc+' baris '+c.row+' tersimpan. '+labels[action]+'.'+(decisions.size===cases.length?' Semua kasus sudah diperiksa.':'');if(current)$('title').focus();
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
