<script>(function(){
const RID='__RID__',SCOPE='__SCOPE__';let state='__STATE__';
const $=id=>document.getElementById(id);
const fmtT=s=>{s=Math.max(0,Math.round(s));const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
 return h?h+'h '+String(m).padStart(2,'0')+'m':m?m+'m '+String(x).padStart(2,'0')+'s':x+'s';};
const KEY='rqb-notify';const getN=()=>{try{return localStorage.getItem(KEY)==='1'}catch(e){return false}};
const setN=v=>{try{localStorage.setItem(KEY,v?'1':'0')}catch(e){}};
const bell=$('bell');
function drawBell(){if(!bell)return;const on=getN()&&window.Notification&&Notification.permission==='granted';
 bell.textContent=on?'🔔 Notifications on':'🔔 Notify me when it finishes';bell.classList.toggle('on',on);}
if(bell){if(!window.Notification){bell.style.display='none';}
 bell.onclick=async()=>{if(getN()&&Notification.permission==='granted'){setN(false);drawBell();return;}
  const p=await Notification.requestPermission();setN(p==='granted');drawBell();
  if(p==='denied')bell.textContent='Notifications blocked in this browser';};drawBell();}
function notify(title,body){
 try{if(getN()&&window.Notification&&Notification.permission==='granted'){
  const n=new Notification(title,{body,icon:'/static/favicon.png',tag:'rqb-'+RID});n.onclick=()=>{window.focus();n.close();};}}catch(e){}
 try{const a=new (window.AudioContext||window.webkitAudioContext)(),o=a.createOscillator(),g=a.createGain();
  o.connect(g);g.connect(a.destination);o.frequency.value=880;g.gain.setValueAtTime(.0001,a.currentTime);
  g.gain.exponentialRampToValueAtTime(.15,a.currentTime+.02);g.gain.exponentialRampToValueAtTime(.0001,a.currentTime+.5);
  o.start();o.stop(a.currentTime+.5);}catch(e){}}
if(!['running','starting'].includes(state))return;
const base=document.title;
async function poll(){let d;try{const r=await fetch('/runs/'+RID+'/live',{cache:'no-store'});d=await r.json();}catch(e){return;}
 document.querySelectorAll('.steps .step').forEach(s=>{const i=+s.dataset.i;s.className='step'+
  (d.state==='done'||i<d.step?' done':i===d.step?(d.state==='waiting'?' waiting':d.state==='error'?' error':' current'):'');});
 const p=d.progress,lab=$('lv-label');const name=d.step_name||'';
 lab.textContent='Step '+d.step+' of 9 · '+name+(p&&p.label?' — '+p.label:'');
 let pct=null;
 if(p&&p.total){pct=Math.min(100,100*p.done/p.total);$('lv-count').textContent=p.done.toLocaleString()+' / '+p.total.toLocaleString()+' AI calls';
  const el=p.updated-p.started;$('lv-eta').textContent=p.done>0&&p.done<p.total?'~'+fmtT(el/p.done*(p.total-p.done))+' left':'';}
 else if(p){$('lv-count').textContent=p.done?p.done+' AI calls':'';$('lv-eta').textContent='';}
 else{$('lv-count').textContent='';$('lv-eta').textContent='';}
 const fill=$('lv-fill');fill.classList.toggle('indet',pct===null);fill.style.width=pct===null?'':pct.toFixed(1)+'%';
 $('lv-el').textContent='· step '+fmtT(d.step_elapsed||0)+' · total '+fmtT(d.elapsed||0);
 document.title='⏳ '+d.step+'/9 · '+SCOPE;
 const lg=$('log');if(lg&&d.log!==undefined){const end=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-20;lg.textContent=d.log;if(end)lg.scrollTop=lg.scrollHeight;}
 if(d.state!==state&&!['running','starting'].includes(d.state)){
  const t={done:'✓ Run finished · '+SCOPE,waiting:'⏸ Waiting for Claude · '+SCOPE,error:'✕ Run stopped · '+SCOPE}[d.state]||SCOPE;
  const b={done:(d.questions||0)+' questions ready. Click to see them.',waiting:(d.pending||0)+' prompts to answer at step '+d.step+'.',
   error:'Error at step '+d.step+' ('+name+'). See the log.'}[d.state]||'';
  notify(t,b);document.title=t;state=d.state;setTimeout(()=>location.reload(),1200);return;}
 state=d.state;}
poll();setInterval(poll,2000);
})();</script>
