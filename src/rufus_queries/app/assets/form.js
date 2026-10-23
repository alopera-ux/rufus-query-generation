const $=id=>document.getElementById(id);
const tree=$('tree'),btn=$('loadcats'),list=$('catlist'),msg=$('catmsg'),q=$('catq'),chips=$('chips'),cnt=$('catcnt');
const esc=t=>t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');
function boxes(){return [...list.querySelectorAll('input[type=checkbox]')];}
function shown(){return boxes().filter(b=>b.closest('label').style.display!=='none');}
const SECS=[...new Set((window.MAPROWS||[]).map(r=>r.s))].sort();
const smap=window.CatMap&&document.getElementById('selmap')?CatMap(document.getElementById('selmap'),{sectors:SECS,empty:'No categories'}):null;
function drawMap(sel){if(!smap)return;const set=new Set(sel);const rows=window.MAPROWS||[];
 smap.set(sel.length?rows.filter(r=>set.has(r.c)):rows);}
function drawChips(){const all=boxes(),s=all.filter(b=>b.checked).map(b=>b.value),sh=shown();drawMap(s);
 cnt.textContent=s.length?s.length+' of '+all.length+' selected':all.length+' categories';
 const filt=sh.length<all.length,allOn=sh.length&&sh.every(b=>b.checked);
 $('catall').disabled=!sh.length;$('catall').textContent=allOn?(filt?'Unselect shown':'Unselect all'):(filt?'Select '+sh.length+' shown':'Select all');
 const chip=c=>'<span class="chip">'+esc(c)+'<button type="button" data-v="'+esc(c)+'" title="Remove">×</button></span>';
 chips.innerHTML=!s.length?'<span class="hint">No category selected: all categories will be processed.</span>'
  :s.length===all.length?'<span class="chip">All '+all.length+' categories</span>'
  :s.slice(0,12).map(chip).join('')+(s.length>12?'<span class="chip more">+'+(s.length-12)+' more</span>':'');}
$('catall').addEventListener('click',()=>{const sh=shown(),on=!(sh.length&&sh.every(b=>b.checked));sh.forEach(b=>b.checked=on);drawChips();});
document.querySelector('form[action="/run"]').addEventListener('submit',()=>{const all=boxes();
 if(all.length&&all.every(b=>b.checked))all.forEach(b=>b.disabled=true);});
list.addEventListener('change',drawChips);
chips.addEventListener('click',e=>{const v=e.target.dataset&&e.target.dataset.v;if(!v)return;
 const b=boxes().find(x=>x.value===v);if(b){b.checked=false;drawChips();}});
$('catclear').addEventListener('click',()=>{boxes().forEach(b=>b.checked=false);drawChips();});
q.addEventListener('input',()=>{const t=q.value.toLowerCase();
 for(const l of list.children){l.style.display=!t||l.textContent.toLowerCase().includes(t)?'':'none';}drawChips();});
function row(c,on){const i=c.indexOf(' > '),sec=i>0?c.slice(0,i):'',cat=i>0?c.slice(i+3):c;
 return '<label class="ci"><input type="checkbox" name="categories" value="'+esc(c)+'"'+(on?' checked':'')+'><span>'+esc(sec)+' ›</span> <b>'+esc(cat)+'</b></label>';}
async function loadCats(){if(!tree.files.length){msg.textContent='Choose a tree file first.';return;}
 msg.textContent='Reading the tree…';btn.disabled=true;const fd=new FormData();fd.append('tree',tree.files[0]);
 try{const r=await fetch('/categories',{method:'POST',body:fd});const d=await r.json();if(!r.ok)throw new Error(d.detail||'error');
  const keep=new Set(boxes().filter(b=>b.checked).map(b=>b.value));list.innerHTML=d.categories.map(c=>row(c,keep.has(c))).join('');
  if(d.map)window.MAPROWS=d.map;
  msg.textContent=d.categories.length+' categories · '+d.leaves.toLocaleString()+' leaves loaded';q.value='';drawChips();
 }catch(e){msg.textContent='Could not read the tree: '+e.message;}btn.disabled=false;}
btn.addEventListener('click',loadCats);
function fname(inp,lab){inp.addEventListener('change',()=>{const f=inp.files[0];const l=$(lab);
 l.textContent=f?f.name+' · '+(f.size/1048576).toFixed(1)+' MB':l.dataset.d;l.classList.toggle('set',!!f);});$(lab).dataset.d=$(lab).textContent;}
fname(tree,'tree_n');fname($('kw'),'kw_n');tree.addEventListener('change',loadCats);
const ni=$('ni'),qpi=$('qpi'),ov=$('ov'),tot=$('tot');
function total(){let n=+ni.value||0,k=+qpi.value||0,t=n*k;const m={};
 for(const p of ov.value.split(/[,;]/)){const x=p.split('=');if(x.length==2&&/^\s*\d+\s*$/.test(x[1]))m[x[0].trim().toUpperCase()]=+x[1];}
 t+=Object.values(m).reduce((a,v)=>a+v-k,0);
 tot.innerHTML='<b>'+t+'</b> questions per category &nbsp;·&nbsp; '+n+' intents × '+k+(Object.keys(m).length?' (with per-intent adjustments)':'');}
[ni,qpi,ov].forEach(e=>e.addEventListener('input',total));total();drawChips();
