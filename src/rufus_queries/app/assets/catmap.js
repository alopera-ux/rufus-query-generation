window.CatMap=function(root,opt){opt=opt||{};
 const PAL=['#A1E335','#FFC200','#B57DFF','#FF87D4','#5AB4F0','#FF9A5C','#3DBB8E','#E8DEFC','#FFE8B2','#D5FFBA','#D9F0FF','#FFCFED'];
 root.innerHTML='<div class="cm-top"><div class="cm-crumbs"></div><div class="cm-note"></div></div><div class="cm"></div>';
 const el=root.querySelector('.cm'),cr=root.querySelector('.cm-crumbs'),note=root.querySelector('.cm-note');
 let tip=document.querySelector('.cm-tip');if(!tip){tip=document.createElement('div');tip.className='cm-tip';document.body.appendChild(tip);}
 const fmt=v=>v>=1e6?(v/1e6).toFixed(1)+'M':v>=1e3?Math.round(v/1e3)+'K':Math.round(v)+'';
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let rows=[],secCol={},view=['all'];
 const vol=()=>rows.length&&rows.every(r=>r.vol!==undefined);
 const sizeOf=r=>vol()?r.vol:r.n;
 function mix(h,a){const n=parseInt(h.slice(1),16);const k=c=>Math.round(c*a+10*(1-a));return 'rgb('+k(n>>16)+','+k(n>>8&255)+','+k(n&255)+')';}
 function showTip(e,h){tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-tip.offsetWidth-8)+'px';
  tip.style.top=Math.min(e.clientY+14,innerHeight-tip.offsetHeight-8)+'px';}
 const hide=()=>tip.style.display='none';
 function lay(data,pad){const W=el.clientWidth||800,H=el.clientHeight||420;
  const r=d3.hierarchy(data).sum(d=>d.children?0:(d.value||0)).sort((a,b)=>b.value-a.value);
  d3.treemap().size([W,H]).paddingInner(2).paddingTop(pad).round(true).tile(d3.treemapSquarify.ratio(1.3))(r);return r;}
 function box(n,cls,bg,html){const d=document.createElement('div');d.className='cm-t '+cls;
  Object.assign(d.style,{left:n.x0+'px',top:n.y0+'px',width:Math.max(0,n.x1-n.x0)+'px',height:Math.max(0,n.y1-n.y0)+'px',background:bg});
  d.innerHTML=html;el.appendChild(d);return d;}
 function head(n,text,fn){const d=document.createElement('div');d.className='cm-h';
  Object.assign(d.style,{left:n.x0+'px',top:n.y0+'px',width:Math.max(0,n.x1-n.x0)+'px'});d.textContent=text;if(fn)d.onclick=fn;el.appendChild(d);}
 function crumbs(list){cr.innerHTML=list.map((p,i)=>i<list.length-1?'<a data-i="'+i+'">'+esc(p[0])+'</a><i>›</i>':'<b>'+esc(p[0])+'</b>').join('');
  cr.querySelectorAll('a').forEach(a=>a.onclick=()=>list[+a.dataset.i][1]());}
 const unit=()=>vol()?'searches':'leaves';
 function all(){view=['all'];el.innerHTML='';hide();
  if(!rows.length){el.innerHTML='<div class="cm-empty">'+(opt.empty||'Nothing to show')+'</div>';crumbs([['—',all]]);note.textContent='';return;}
  const secs={};rows.forEach(r=>(secs[r.s]=secs[r.s]||[]).push(r));
  const one=Object.keys(secs).length===1;
  const R=lay({children:Object.entries(secs).map(([s,rs])=>({name:s,children:rs.map(r=>({name:r.c,row:r,value:sizeOf(r)}))}))},18);
  R.children.forEach(s=>{const tot=s.value;head(s,s.data.name+' · '+(vol()?fmt(tot)+' searches':tot.toLocaleString()+' leaves'),()=>sector(s.data.name));
   s.children.forEach(c=>{const r=c.data.row,big=(c.x1-c.x0)>70&&(c.y1-c.y0)>34,name=r.c.split(' > ').slice(1).join(' > ');
    const t=box(c,vol()?'':'dim',vol()?secCol[r.s]:mix(secCol[r.s],.42),big?'<b>'+esc(name)+'</b><small>'+(vol()?fmt(r.vol)+' · ':'')+r.n+' leaves</small>':'');
    t.onmousemove=e=>showTip(e,'<b>'+esc(name)+'</b><span>'+esc(r.s)+'</span>'+r.n+' leaves'+(vol()?' · <b style="display:inline;color:#A1E335">'+fmt(r.vol)+' searches</b>':'')+'<span>Click to open</span>');
    t.onmouseleave=hide;t.onclick=()=>cat(r);});});
  crumbs([[rows.length+' categor'+(rows.length===1?'y':'ies')+' · '+Object.keys(secs).length+' sector'+(one?'':'s'),all]]);
  note.textContent=(vol()?'Size = real search volume.':'Size = number of leaves (no volume until the run classifies the keywords).')+' Click a sector or a category.';
  if(opt.onView)opt.onView('all',null);}
 function sector(sn){view=['sector',sn];el.innerHTML='';hide();const rs=rows.filter(r=>r.s===sn);
  const R=lay({children:rs.map(r=>({name:r.c,row:r,children:r.b.map(b=>({name:b.name,value:b.leaves?b.leaves.reduce((a,l)=>a+l.value,0):b.value,n:b.leaves?b.leaves.length:b.value}))}))},18);
  R.children.forEach(c=>{const r=c.data.row,name=r.c.split(' > ').slice(1).join(' > ');
   head(c,name+' · '+(vol()?fmt(r.vol):r.n+' leaves'),()=>cat(r));
   (c.children||[]).forEach(b=>{const big=(b.x1-b.x0)>60&&(b.y1-b.y0)>30;
    const t=box(b,vol()?'':'dim',vol()?mix(secCol[sn],.8):mix(secCol[sn],.32),big?'<b>'+esc(b.data.name)+'</b><small>'+(vol()?fmt(b.value)+' · ':'')+b.data.n+' leaves</small>':'');
    t.onmousemove=e=>showTip(e,'<b>'+esc(b.data.name)+'</b><span>'+esc(name)+'</span>'+(vol()?fmt(b.value)+' searches · ':'')+b.data.n+' leaves');
    t.onmouseleave=hide;t.onclick=()=>cat(r);});});
  crumbs([['All',all],[sn,()=>sector(sn)]]);note.textContent='Each category with its branches.';
  if(opt.onView)opt.onView('sector',sn);}
 function cat(r){view=['cat',r.c];el.innerHTML='';hide();const name=r.c.split(' > ').slice(1).join(' > ');
  const bc={};r.b.forEach((b,i)=>bc[b.name]=PAL[i%PAL.length]);
  if(vol()){const R=lay({children:r.b.map(b=>({name:b.name,children:b.leaves}))},18);
   R.children.forEach(b=>{head(b,b.data.name+' · '+fmt(b.value));
    b.children.forEach(l=>{const d=l.data,big=(l.x1-l.x0)>64&&(l.y1-l.y0)>30;
     const t=box(l,d.focus?'':'out',bc[b.data.name],big?'<b>'+esc(d.name)+'</b><small>'+fmt(d.value)+' · '+d.pct.toFixed(1)+'%</small>'+(d.focus?'<span class="tag">focus</span>':''):'');
     t.onmousemove=e=>showTip(e,'<b>'+esc(d.name)+'</b><span>'+esc(b.data.name)+'</span>'+fmt(d.value)+' searches · '+d.pct.toFixed(1)+'% of the category'
      +(d.focus?'<span style="color:#A1E335">In the focus</span>':'<span>Outside the focus</span>')+(opt.onLeaf?'<span>Click to see its keywords</span>':''));
     t.onmouseleave=hide;if(opt.onLeaf)t.onclick=()=>{hide();opt.onLeaf(r.c,d.code);};});});
   note.innerHTML='Leaves by real search volume. Faded = outside the focus.'+(opt.onCat?' <a class="cm-go">Open its universe and questions ↓</a>':'');}
  else{const R=lay({children:r.b.map(b=>({name:b.name,value:b.value}))},0);
   R.children.forEach(b=>{const big=(b.x1-b.x0)>60&&(b.y1-b.y0)>30;
    const t=box(b,'dim',mix(secCol[r.s],.45),big?'<b>'+esc(b.data.name)+'</b><small>'+b.value+' leaves</small>':'');
    t.onmousemove=e=>showTip(e,'<b>'+esc(b.data.name)+'</b>'+b.value+' leaves');t.onmouseleave=hide;});
   note.textContent='Branches by number of leaves.';}
  const go=note.querySelector('.cm-go');if(go){go.style.cursor='pointer';go.style.color='#A1E335';go.onclick=()=>opt.onCat(r.c);}
  crumbs([['All',all],[r.s,()=>sector(r.s)],[name,()=>cat(r)]]);
  if(opt.onView)opt.onView('cat',r.c);}
 function set(rs){rows=rs||[];const ss=[...new Set(rows.map(r=>r.s))].sort();secCol={};
  (opt.sectors||ss).forEach((s,i)=>secCol[s]=PAL[i%PAL.length]);ss.forEach(s=>{if(!secCol[s])secCol[s]=PAL[Object.keys(secCol).length%PAL.length];});
  if(view[0]==='sector'&&rows.some(r=>r.s===view[1]))sector(view[1]);
  else if(view[0]==='cat'){const r=rows.find(x=>x.c===view[1]);r?cat(r):all();}else all();}
 let rt;addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(()=>set(rows),200);});
 return {set,open:c=>{const r=rows.find(x=>x.c===c);if(r)cat(r);}};};
