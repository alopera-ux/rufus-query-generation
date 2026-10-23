<div class='utip' id='utip'></div><div class='kwshade' id='kwshade'></div>
<aside class='kwdrawer' id='kwdrawer' aria-hidden='true'><div class='kwhead'><h3><span id='kwname'></span>
<button class='x' id='kwclose' type='button' aria-label='Close'>×</button></h3><div class='path' id='kwpath'></div>
<div class='kwstats' id='kwstats'></div></div>
<div class='kwtools'><input type='text' id='kwq' placeholder='Filter keywords…'><span class='hint' id='kwcount' style='margin:0'></span></div>
<div class='kwf' id='kwf'></div><div class='kwbody' id='kwbody'></div></aside>
<script>(function(){
const tip=document.getElementById('utip'),dr=document.getElementById('kwdrawer'),sh=document.getElementById('kwshade');
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>v>=1e6?(v/1e6).toFixed(1)+'M':v>=1e3?Math.round(v/1e3)+'K':String(Math.round(v));
let data=null,flt='all';
document.querySelectorAll('.uni .bar').forEach(b=>{
 b.addEventListener('mousemove',e=>{const d=b.dataset;
  tip.innerHTML='<b>'+esc(d.name)+'</b><span>'+esc(d.branch)+'</span><em>'+d.vol+'</em> searches · '+d.pct+'% of the category'
   +'<span>'+d.n+' keywords'+(d.focus==='1'?' · in focus':' · outside focus')+'</span>'
   +(d.merged?'<span style="color:#fff">⇄ Merged with sibling: '+esc(d.merged)+'</span>':'')+'<small>Click to see the keywords</small>';
  tip.style.display='block';const w=tip.offsetWidth,h=tip.offsetHeight;
  tip.style.left=Math.min(e.clientX+14,innerWidth-w-8)+'px';tip.style.top=Math.max(8,Math.min(e.clientY+14,innerHeight-h-8))+'px';});
 b.addEventListener('mouseleave',()=>tip.style.display='none');
 b.addEventListener('click',()=>{tip.style.display='none';
  document.querySelectorAll('.uni .bar.sel').forEach(x=>x.classList.remove('sel'));b.classList.add('sel');
  open(b.closest('.uni').dataset.cat,b.dataset.code);});
});
function close(){dr.classList.remove('open');sh.classList.remove('open');dr.setAttribute('aria-hidden','true');}
document.getElementById('kwclose').onclick=close;sh.onclick=close;
document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
document.getElementById('kwq').addEventListener('input',draw);
window.uniOpen=(cat,code)=>open(cat,code);
window.uniShow=cat=>{const u=[...document.querySelectorAll('.unipane .uni')].find(x=>x.dataset.cat===cat);if(!u)return;
 const i=u.closest('.unipane').dataset.i,b=document.querySelector('.unitabs button[data-i="'+i+'"]');if(b)b.click();
 u.closest('.card').scrollIntoView({behavior:'smooth',block:'start'});};
function pageScope(level,val){
 const inScope=c=>level==='all'||(level==='sector'?c.startsWith(val+' > '):c===val);
 document.querySelectorAll('.catblock').forEach(b=>b.style.display=inScope(b.dataset.cat)?'':'none');
 document.querySelectorAll('.unitabs button').forEach(b=>b.style.display=inScope(b.dataset.cat)?'':'none');
 const first=[...document.querySelectorAll('.unitabs button')].find(b=>inScope(b.dataset.cat));
 if(first&&!document.querySelector('.unitabs button.on:not([style*="none"])'))first.click();
 if(level==='cat'){const b=document.querySelector('.unitabs button[data-cat="'+CSS.escape(val)+'"]');if(b)b.click();}
 const lbl=level==='all'?'':(level==='sector'?val:val.split(' > ').slice(1).join(' > '));
 document.querySelectorAll('.scopelbl').forEach(x=>{x.textContent=lbl?'Showing: '+lbl:'';x.style.display=lbl?'':'none';});}
{const bb=document.getElementById('bdbtn'),box=document.getElementById('runmap');let cm=null;
 if(bb&&box&&window.CatMap)bb.onclick=()=>{const on=box.style.display==='none';box.style.display=on?'':'none';
  bb.textContent=on?'✕ Close breakdown':'▦ Break down';bb.classList.toggle('on',on);
  if(on&&!cm){cm=CatMap(box,{sectors:window.RUNSECS,onLeaf:(c,k)=>open(c,k),onView:pageScope,onCat:c=>{box.style.display='none';bb.textContent='▦ Break down';bb.classList.remove('on');pageScope('cat',c);window.uniShow(c);}});cm.set(window.RUNMAP);}
  else if(on)cm.set(window.RUNMAP);
  if(!on)pageScope('all',null);};}
document.querySelectorAll('.qrow').forEach(r=>{const g=r.closest('.qgrid');if(!g)return;
 const on=v=>{g.classList.toggle('dim',v);g.querySelectorAll('.bar').forEach(b=>b.classList.toggle('sel',v&&r.dataset.codes.split('+').includes(b.dataset.code)));};
 r.addEventListener('mouseenter',()=>on(true));r.addEventListener('mouseleave',()=>on(false));});
async function open(cat,code){
 dr.classList.add('open');sh.classList.add('open');dr.setAttribute('aria-hidden','false');
 document.getElementById('kwname').textContent='Loading…';document.getElementById('kwbody').innerHTML='';
 const base=location.pathname.replace(/\/$/,'');
 const r=await fetch(base+'/leaf?cat='+encodeURIComponent(cat)+'&code='+encodeURIComponent(code));
 if(!r.ok){document.getElementById('kwname').textContent='Could not load this leaf';return;}
 data=await r.json();flt='all';document.getElementById('kwq').value='';
 document.getElementById('kwname').textContent=data.leaf;
 document.getElementById('kwpath').innerHTML=esc(data.path)+(data.merged?'<div class="kwsib">⇄ Merged with its sibling <b>'
  +esc(data.merged.with)+'</b> to choose questions: '+esc(data.merged.reason)+'</div>':'');
 document.getElementById('kwstats').innerHTML='<div>Total volume<b>'+fmt(data.total)+'</b></div><div>Own keywords<b>'+fmt(data.own)
  +'</b></div><div>From parent nodes<b>'+fmt(data.from_nodes)+'</b></div>';
 const ints={};data.keywords.forEach(k=>{ints[k.intent]=ints[k.intent]||{n:k.intent_name,v:0};ints[k.intent].v+=k.volume;});
 const order=Object.entries(ints).sort((a,b)=>b[1].v-a[1].v);
 document.getElementById('kwf').innerHTML='<button type="button" data-f="all" class="on">All</button>'
  +order.map(([c,o])=>'<button type="button" data-f="'+esc(c)+'">'+esc(o.n)+' · '+fmt(o.v)+'</button>').join('');
 document.querySelectorAll('#kwf button').forEach(x=>x.onclick=()=>{flt=x.dataset.f;
  document.querySelectorAll('#kwf button').forEach(y=>y.classList.toggle('on',y===x));draw();});
 draw();}
function draw(){if(!data)return;const q=document.getElementById('kwq').value.toLowerCase().trim();
 const ks=data.keywords.filter(k=>(flt==='all'||k.intent===flt)&&(!q||k.keyword.toLowerCase().includes(q)));
 const mx=Math.max(1,...data.keywords.map(k=>k.volume));const sv=ks.reduce((a,k)=>a+k.volume,0);
 document.getElementById('kwcount').textContent=ks.length+' of '+data.keywords.length+' · '+fmt(sv)+' searches';
 const row=k=>'<tr><td>'+esc(k.keyword)+(k.brand?'<span class="bt">'+esc(k.brand)+'</span>':'')+'</td><td><span class="it '+esc(k.intent)+'">'
  +esc(k.intent_name)+'</span>'+(k.need?'<div class="hint" style="margin-top:3px">'+esc(k.need)+'</div>':'')+'</td><td style="white-space:nowrap">'
  +'<span class="bar-w" style="width:60px"><i style="width:'+(100*k.volume/mx).toFixed(1)+'%"></i></span>'+fmt(k.volume)+'</td></tr>';
 let h='<table><thead><tr><th>Keyword</th><th>Intent</th><th>Volume</th></tr></thead><tbody>'+ks.map(row).join('')+'</tbody></table>';
 if(!ks.length)h='<p class="empty" style="padding:16px 0">No keywords match.</p>';
 if(data.parents.length&&flt==='all'&&!q){h+='<h4>Keywords from parent nodes</h4><p class="note">These keywords were classified at a higher level of the tree '
  +'(for example a whole branch). Their volume is shared among the leaves below in proportion to each leaf\'s own volume; the column shows the part this leaf receives.</p>'
  +'<table><thead><tr><th>Keyword</th><th>Classified at</th><th>To this leaf</th></tr></thead><tbody>'
  +data.parents.map(k=>'<tr><td>'+esc(k.keyword)+'<div class="hint">'+fmt(k.volume)+' in total</div></td><td>'+esc(k.node)+'</td><td>'+fmt(k.share)+'</td></tr>').join('')
  +'</tbody></table>';}
 document.getElementById('kwbody').innerHTML=h;}
})();</script>
