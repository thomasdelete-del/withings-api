from __future__ import annotations


def dashboard_html() -> str:
    return r'''<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="light">
  <title>Mein Withings-Dashboard</title>
  <style>
    :root{--ink:#15231f;--muted:#65736e;--paper:#f5f7f4;--card:#fff;--line:#dce5e0;--green:#087b5b;--mint:#dff4eb;--blue:#316b9d;--red:#b14747}
    *{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.45 system-ui,-apple-system,sans-serif}
    main{max-width:980px;margin:auto;padding:24px 16px 48px}header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px}
    h1{font-size:clamp(1.65rem,5vw,2.5rem);line-height:1.05;margin:0 0 6px}.sub{color:var(--muted);margin:0}.status{font-size:.88rem;padding:7px 11px;border-radius:999px;background:var(--mint);color:var(--green);white-space:nowrap}
    .panel,.card{background:var(--card);border:1px solid var(--line);border-radius:18px;box-shadow:0 7px 22px rgba(34,57,49,.06)}
    .login{padding:18px;margin-bottom:18px}.login label{font-weight:650;display:block;margin-bottom:8px}.row{display:flex;gap:10px}.row input{min-width:0;flex:1;border:1px solid #b9c8c1;border-radius:11px;padding:12px;font:inherit}.row button,.ghost{border:0;border-radius:11px;background:var(--green);color:white;padding:12px 18px;font:700 1rem system-ui;cursor:pointer}.hint{font-size:.88rem;color:var(--muted);margin:9px 0 0}
    .error{color:var(--red);margin:12px 0 0}.hidden{display:none!important}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:18px}.card{padding:18px}.label{font-size:.87rem;color:var(--muted)}.value{font-size:clamp(1.55rem,5vw,2.25rem);font-weight:760;letter-spacing:-.03em;margin-top:3px}.delta{font-size:.9rem;margin-top:3px;color:var(--muted)}
    .chart-card{padding:18px;margin-bottom:14px}.chart-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}.chart-head h2{font-size:1.08rem;margin:0}.chart-wrap{height:280px;position:relative}canvas{width:100%;height:100%;display:block}.empty{height:100%;display:grid;place-items:center;color:var(--muted)}
    footer{display:flex;justify-content:space-between;gap:12px;align-items:center;color:var(--muted);font-size:.88rem;margin-top:18px}footer a{color:var(--green)}.ghost{background:transparent;color:var(--green);border:1px solid var(--line);padding:8px 11px;font-size:.88rem}
    @media(max-width:650px){main{padding-top:18px}.cards{grid-template-columns:1fr 1fr}.cards .card:last-child{grid-column:1/-1}.chart-wrap{height:235px}.row{flex-direction:column}.row button{width:100%}footer{align-items:flex-start;flex-direction:column}}
  </style>
</head>
<body><main>
  <header><div><h1>Gesundheitsverlauf</h1><p class="sub">Gewicht und Körperzusammensetzung</p></div><span id="status" class="status">Bereit</span></header>
  <section id="login" class="panel login">
    <label for="key">API-Schlüssel</label><div class="row"><input id="key" type="password" autocomplete="off" placeholder="X-API-Key einfügen"><button id="load">Daten anzeigen</button></div>
    <p class="hint">Der Schlüssel bleibt nur für diese Browser-Sitzung gespeichert.</p><p id="error" class="error hidden"></p>
  </section>
  <section id="content" class="hidden">
    <div class="cards">
      <article class="card"><div class="label">Aktuelles Gewicht</div><div id="weight" class="value">–</div><div id="weightDate" class="delta">–</div></article>
      <article class="card"><div class="label">Veränderung 30 Tage</div><div id="change" class="value">–</div><div class="delta">gegenüber dem ältesten Wert</div></article>
      <article class="card"><div class="label">Körperfett</div><div id="fat" class="value">–</div><div id="fatDate" class="delta">letzter verfügbarer Wert</div></article>
    </div>
    <article class="panel chart-card"><div class="chart-head"><h2>Gewicht · letzte 12 Monate</h2><span id="weightRange" class="label"></span></div><div class="chart-wrap"><canvas id="weightChart"></canvas></div></article>
    <article class="panel chart-card"><div class="chart-head"><h2>Körperfett · letzte 12 Monate</h2><span id="fatRange" class="label"></span></div><div class="chart-wrap"><canvas id="fatChart"></canvas></div></article>
  </section>
  <footer><span id="updated">Noch nicht geladen</span><div><button id="forget" class="ghost hidden">Schlüssel vergessen</button> · <a href="/docs">API</a></div></footer>
</main>
<script>
const $=id=>document.getElementById(id), fmtDate=t=>new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'2-digit',year:'numeric'}).format(new Date(t*1000));
const metricItems=(payload,name)=>payload.items.filter(x=>x.metric===name).sort((a,b)=>a.timestamp-b.timestamp);
async function api(path,key){const r=await fetch(path,{headers:{'X-API-Key':key}});if(!r.ok)throw new Error(r.status===401?'API-Schlüssel ist nicht gültig.':`Serverfehler ${r.status}`);return r.json()}
function thin(items,max=180){if(items.length<=max)return items;const step=(items.length-1)/(max-1);return Array.from({length:max},(_,i)=>items[Math.round(i*step)])}
function draw(canvas,items,color,unit){const wrap=canvas.parentElement,dpr=devicePixelRatio||1,w=wrap.clientWidth,h=wrap.clientHeight;canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);c.clearRect(0,0,w,h);if(!items.length){c.fillStyle='#65736e';c.textAlign='center';c.font='15px system-ui';c.fillText('Keine Werte vorhanden',w/2,h/2);return}const p={l:45,r:12,t:18,b:30},vals=items.map(x=>x.value),min=Math.min(...vals),max=Math.max(...vals),pad=Math.max((max-min)*.15,.3),lo=min-pad,hi=max+pad,x=i=>p.l+i*(w-p.l-p.r)/Math.max(items.length-1,1),y=v=>p.t+(hi-v)*(h-p.t-p.b)/(hi-lo);c.strokeStyle='#dce5e0';c.fillStyle='#65736e';c.font='12px system-ui';c.textAlign='right';for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3,yy=y(v);c.beginPath();c.moveTo(p.l,yy);c.lineTo(w-p.r,yy);c.stroke();c.fillText(v.toFixed(1),p.l-7,yy+4)}c.textAlign='left';c.fillText(fmtDate(items[0].timestamp).slice(0,5),p.l,h-7);c.textAlign='right';c.fillText(fmtDate(items.at(-1).timestamp).slice(0,5),w-p.r,h-7);c.strokeStyle=color;c.lineWidth=2.5;c.lineJoin='round';c.beginPath();items.forEach((v,i)=>i?c.lineTo(x(i),y(v.value)):c.moveTo(x(i),y(v.value)));c.stroke();const last=items.at(-1);c.fillStyle=color;c.beginPath();c.arc(x(items.length-1),y(last.value),4,0,Math.PI*2);c.fill();c.textAlign='right';c.font='600 12px system-ui';c.fillText(`${last.value.toFixed(1)} ${unit}`,w-p.r,y(last.value)-9)}
async function load(){const key=$('key').value.trim();if(!key)return showError('Bitte den API-Schlüssel einfügen.');$('status').textContent='Lade …';$('error').classList.add('hidden');try{const since=new Date();since.setFullYear(since.getFullYear()-1);const data=await api(`/api/v1/measurements?from=${encodeURIComponent(since.toISOString())}&limit=10000&ascending=true`,key);sessionStorage.setItem('withingsApiKey',key);const weights=metricItems(data,'weight'),fats=metricItems(data,'fat_ratio'),latestW=weights.at(-1),latestF=fats.at(-1);$('weight').textContent=latestW?`${latestW.value.toFixed(1)} kg`:'–';$('weightDate').textContent=latestW?fmtDate(latestW.timestamp):'Kein Wert';$('fat').textContent=latestF?`${latestF.value.toFixed(1)} %`:'–';$('fatDate').textContent=latestF?fmtDate(latestF.timestamp):'Kein Wert';const cutoff=Date.now()/1000-30*86400,recent=weights.filter(x=>x.timestamp>=cutoff);$('change').textContent=recent.length>1?`${(recent.at(-1).value-recent[0].value)>=0?'+':''}${(recent.at(-1).value-recent[0].value).toFixed(1)} kg`:'–';$('weightRange').textContent=weights.length?`${weights.length} Messungen`:'';$('fatRange').textContent=fats.length?`${fats.length} Messungen`:'';$('content').classList.remove('hidden');$('login').classList.add('hidden');$('forget').classList.remove('hidden');$('status').textContent='Aktuell';$('updated').textContent=`Geladen: ${new Intl.DateTimeFormat('de-DE',{dateStyle:'medium',timeStyle:'short'}).format(new Date())}`;requestAnimationFrame(()=>{draw($('weightChart'),thin(weights),'#087b5b','kg');draw($('fatChart'),thin(fats),'#316b9d','%')})}catch(e){$('status').textContent='Fehler';showError(e.message)}}
function showError(msg){$('error').textContent=msg;$('error').classList.remove('hidden')}$('load').onclick=load;$('key').onkeydown=e=>{if(e.key==='Enter')load()};$('forget').onclick=()=>{sessionStorage.removeItem('withingsApiKey');location.reload()};const saved=sessionStorage.getItem('withingsApiKey');if(saved){$('key').value=saved;load()}addEventListener('resize',()=>{if(!$('content').classList.contains('hidden'))load()});
</script></body></html>'''
