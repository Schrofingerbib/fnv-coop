"""Single-file static HTML dashboard (no external deps): Overview | Backlog | Sweeps | Quests."""
import json

COLORS = {'unknown': '#8a8f98', 'spec': '#6b9bd1', 'sim-pass': '#53b3a1', 'game-pass': '#4caf50',
          'playtested': '#2e7d32', 'failing': '#d9534f'}

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FNV Co-op Readiness</title>
<style>
:root{--bg:#fafafa;--fg:#1d2127;--mut:#5f6773;--card:#fff;--bd:#dcdfe4;--ok:#2e7d32;--warn:#b26a00;--bad:#c62828;
%(vars)s}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e6e8eb;--mut:#9aa3ae;--card:#1c2025;--bd:#2c323a;--ok:#66bb6a;--warn:#ffb74d;--bad:#ef5350}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:22px 0 8px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:12px;margin-bottom:12px;overflow-x:auto}
.tiles{display:flex;flex-wrap:wrap;gap:8px}.tile{flex:1 1 130px;border:1px solid var(--bd);border-radius:8px;padding:8px;background:var(--card)}
.tile b{font-size:22px;display:block}
table{border-collapse:collapse;width:100%%}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--bd);white-space:nowrap}
.bar{display:flex;height:14px;border-radius:3px;overflow:hidden;min-width:160px;background:var(--bd)}
.bar i{display:block;height:100%%}
.chip{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;color:#fff}
.legend span{margin-right:12px}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px}
.path{display:flex;flex-wrap:wrap;gap:4px}
input,select,button{background:var(--card);color:var(--fg);border:1px solid var(--bd);border-radius:4px;padding:4px 8px;font:inherit}
.tabs{display:flex;gap:6px;margin:12px 0;flex-wrap:wrap}.tabs button{cursor:pointer}.tabs button.on{background:var(--fg);color:var(--bg)}
.ready{color:var(--ok);font-weight:600}.sev-blocker{color:var(--bad);font-weight:600}.sev-high{color:var(--warn);font-weight:600}
.pane{display:none}.pane.on{display:block}
.num{text-align:right}
</style></head><body>
<h1>FNV co-op readiness</h1>
<div class="mut" id="meta"></div>
<div class="tabs" id="tabs"></div>

<div class="pane" id="p-overview">
<div class="tiles" id="head"></div>
<div class="legend card" id="legend"></div>
<h2>Story quests by coverage rung</h2><div class="tiles" id="tiles"></div>
<h2>By tier</h2><div class="card"><table id="tier"></table></div>
<h2>By questline</h2><div class="card"><table id="line"></table></div>
<h2>Critical path per ending</h2><p class="mut">* = auto-derived prerequisite (found in the script graph, not hand-curated).</p><div id="paths"></div>
<h2>Fastest order to main story co-op</h2><div class="card"><table id="plan"></table></div>
</div>

<div class="pane" id="p-backlog">
<div class="card" id="bl-head"></div>
<h2>Work items (one change fixes several functions)</h2><p class="mut">Rank = game-wide call count x severity weight (blocker 8, high 4, medium 2, low 1). Status recomputed from the current source.</p>
<div class="card"><table id="bl-work"></table></div>
<h2>Functions not fully synced</h2><div class="card"><table id="bl-fn"></table></div>
<h2>Per questline: %% of calls synced and remaining blockers</h2><div class="card"><table id="bl-ql"></table></div>
</div>

<div class="pane" id="p-sweeps">
<h2>Stage sweeps per questline</h2><p class="mut" id="sw-note"></p>
<div class="card"><table id="sw-ql"></table></div>
<h2>Quests with failing or errored stages</h2><div class="card"><table id="sw-bad"></table></div>
</div>

<div class="pane" id="p-quests">
<div class="card"><p><input id="q" placeholder="filter id/name"> <select id="st"><option value="">any status</option></select>
<select id="tr"><option value="">any tier</option></select> <label><input type="checkbox" id="so" checked> story only</label></p>
<table id="tbl"></table></div>
</div>

<script>
const R=%(data)s, C=%(colors)s, B=['unknown','spec','sim-pass','game-pass','playtested','failing'];
const $=id=>document.getElementById(id);
const chip=b=>`<span class="chip" style="background:${C[b]}">${b}</span>`;
const bar=c=>{const t=B.reduce((s,b)=>s+c[b],0)||1;return `<div class="bar" title="${B.map(b=>b+':'+c[b]).join(' ')}">`+B.map(b=>c[b]?`<i style="width:${100*c[b]/t}%%;background:${C[b]}"></i>`:'').join('')+'</div>'};
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));
const pct=(a,b)=>b?(100*a/b).toFixed(1)+'%%':'-';
const sevc=s=>`<span class="sev-${s}">${s}</span>`;
const multibar=(parts)=>{const t=parts.reduce((s,p)=>s+p[1],0)||1;return `<div class="bar" title="${parts.map(p=>p[0]+':'+p[1]).join(' ')}">`+parts.map(p=>p[1]?`<i style="width:${100*p[1]/t}%%;background:${p[2]}"></i>`:'').join('')+'</div>'};
$('meta').textContent=`generated ${R.generated} | ${R.runs} run summaries | target ${R.target} | ${R.story_total} story / ${R.all_total} quests`;
const tabs=[['overview','Overview'],['backlog','Backlog'],['sweeps','Sweeps'],['quests','Quests']];
$('tabs').innerHTML=tabs.map(t=>`<button data-t="${t[0]}">${t[1]}</button>`).join('');
const show=t=>{tabs.forEach(x=>{$('p-'+x[0]).classList.toggle('on',x[0]==t)});document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t==t));try{location.hash=t}catch(e){}};
$('tabs').addEventListener('click',e=>{if(e.target.dataset.t)show(e.target.dataset.t)});
show((location.hash||'#overview').slice(1).replace(/[^a-z]/g,'')||'overview');
if(!document.querySelector('.pane.on'))show('overview');

// ---- overview
const BL=R.backlog, SW=R.sweeps;
const swT=SW?Object.values(SW.questlines).reduce((a,q)=>({p:a.p+q.planned,ok:a.ok+q.pass,f:a.f+q.fail+q.error}),{p:0,ok:0,f:0}):null;
const heads=[];
if(BL)heads.push([BL.game_wide.synced_pct+'%%','script calls synced (live source)',BL.game_wide.synced_pct>=90?'var(--ok)':'var(--warn)']);
if(swT)heads.push([swT.ok+' / '+swT.p,'sweep stages passed ('+swT.f+' failing)',swT.f?'var(--bad)':'var(--fg)']);
heads.push([R.totals['game-pass']+R.totals['playtested']+' / '+R.story_total,'story quests at game-pass or better',R.totals.failing?'var(--bad)':'var(--fg)']);
heads.push([R.totals.failing,'quests failing',R.totals.failing?'var(--bad)':'var(--ok)']);
const ready=Object.values(R.paths).filter(p=>p.ready).length;
heads.push([ready+' / '+Object.keys(R.paths).length,'endings fully proven',ready?'var(--ok)':'var(--fg)']);
$('head').innerHTML=heads.map(h=>`<div class="tile"><b style="color:${h[2]}">${h[0]}</b>${h[1]}</div>`).join('');
$('legend').innerHTML=B.map(b=>`<span><i style="background:${C[b]}"></i>${b}</span>`).join('');
$('tiles').innerHTML=B.map(b=>`<div class="tile"><b style="color:${C[b]}">${R.totals[b]}</b>${b}</div>`).join('');
const grid=(el,obj,first)=>{$(el).innerHTML=`<tr><th>${first}</th><th>n</th><th>progress</th>`+B.map(b=>`<th>${b}</th>`).join('')+'</tr>'+
 Object.entries(obj).map(([k,c])=>`<tr><td>${esc(k)}</td><td>${B.reduce((s,b)=>s+c[b],0)}</td><td>${bar(c)}</td>`+B.map(b=>`<td>${c[b]||''}</td>`).join('')+'</tr>').join('')};
grid('tier',R.by_tier,'Tier');grid('line',R.by_line,'Questline');
$('paths').innerHTML=Object.entries(R.paths).map(([e,p])=>`<div class="card"><b>${esc(e)}</b> <span class="${p.ready?'ready':'mut'}">${p.ready?'READY':p.done+'/'+p.total+' at '+R.target}</span>${p.failing.length?` <span class="chip" style="background:${C.failing}">failing: ${esc(p.failing.join(', '))}</span>`:''}
<div class="path">${p.quests.map(q=>`<span class="chip" style="background:${C[q.bucket]}" title="${esc(q.name)} ${q.tier}${q.discovered?' (auto-derived)':''}">${esc(q.edid)} ${q.tier}${q.discovered?'*':''}</span>`).join('')}</div></div>`).join('');
$('plan').innerHTML='<tr><th>#</th><th>Quest</th><th>Tier</th><th>Needed by</th><th>Status</th><th>Completes</th></tr>'+R.plan.map(s=>`<tr><td>${s.order}</td><td>${esc(s.edid)} <span class="mut">${esc(s.name)}</span></td><td>${s.tier}</td><td>${esc(s.needed_by.join(', '))}</td><td>${chip(s.bucket)}</td><td>${esc(s.unlocks_ending)}</td></tr>`).join('');

// ---- backlog
if(BL){const g=BL.game_wide,c=g.calls;
 $('bl-head').innerHTML=`<b>${g.synced_pct}%%</b> of ${(c.supported+c.partial+c.missing).toLocaleString()} counted script calls are fully synced (static census tables claimed ${g.synced_pct_static}%%; ${g.functions_changed} functions changed status since). `+
  multibar([['supported',c.supported,'#4caf50'],['partial',c.partial,'#ffb74d'],['missing',c.missing,'#d9534f']])+
  `<div class="mut">scanned ${BL.source.files_scanned} source files, ${BL.source.effect_types} Effect types, ${BL.source.hooked_commands} hooked commands; local/intentionally unreplicated calls: ${c.local||0}</div>`;
 $('bl-work').innerHTML='<tr><th>#</th><th>action</th><th>target</th><th class="num">score</th><th class="num">calls</th><th>severity</th><th>functions</th></tr>'+
  BL.work_items.slice(0,40).map(w=>`<tr><td>${w.rank}</td><td>${esc(w.action)}</td><td>${esc(w.target)}</td><td class="num">${w.score}</td><td class="num">${w.calls}</td><td>${sevc(w.max_severity)}</td><td>${esc(w.functions.slice(0,6).join(', '))}${w.functions.length>6?'...':''}</td></tr>`).join('');
 $('bl-fn').innerHTML='<tr><th>#</th><th>function</th><th class="num">calls</th><th>severity</th><th>static &rarr; live</th><th>effect</th><th>capture</th><th>apply</th></tr>'+
  BL.functions.filter(f=>f.score>0).slice(0,60).map(f=>`<tr><td>${f.rank}</td><td>${esc(f.fn)}</td><td class="num">${f.count}</td><td>${sevc(f.severity)}</td><td>${f.static} &rarr; <b>${f.live}</b></td><td>${esc(f.effect_types.join('/')||'-')}</td><td>${f.captured}</td><td>${f.applied}</td></tr>`).join('');
 $('bl-ql').innerHTML='<tr><th>questline</th><th class="num">counted calls</th><th>synced</th><th class="num">%%</th><th class="num">blocker calls</th><th>top blockers</th></tr>'+
  Object.entries(BL.questlines).map(([k,q])=>`<tr><td>${esc(k)}</td><td class="num">${q.counted_calls}</td><td>${q.gap_only_table?'<span class="mut">gap-only table</span>':multibar([['supported',q.calls.supported||0,'#4caf50'],['partial',q.calls.partial||0,'#ffb74d'],['missing',q.calls.missing||0,'#d9534f']])}</td><td class="num">${q.synced_pct==null?'-':q.synced_pct}</td><td class="num">${q.blocker_calls}</td><td>${esc(q.blockers.slice(0,4).map(r=>r.fn+'('+r.count+')').join(', '))}</td></tr>`).join('');
}else $('p-backlog').innerHTML='<p class="mut">no backlog data (questlines/systems/census.json missing)</p>';

// ---- sweeps
if(SW){
 $('sw-note').textContent=`${SW.results_total} stage results read (out/sweeps, out/goodsprings_sweep). Planned = stages in each questline's sweep.json.`;
 $('sw-ql').innerHTML='<tr><th>questline</th><th class="num">planned</th><th>progress</th><th class="num">pass</th><th class="num">fail</th><th class="num">error</th><th class="num">noent</th><th class="num">not run</th><th class="num">pass %%</th></tr>'+
  Object.entries(SW.questlines).map(([k,q])=>`<tr><td>${esc(k)}</td><td class="num">${q.planned}</td><td>${multibar([['pass',q.pass,'#4caf50'],['fail',q.fail,'#d9534f'],['error',q.error,'#b71c1c'],['noent',q.noent,'#8a8f98'],['not run',q.not_run,'#cfd3d9']])}</td><td class="num">${q.pass}</td><td class="num">${q.fail}</td><td class="num">${q.error}</td><td class="num">${q.noent}</td><td class="num">${q.not_run}</td><td class="num">${q.pass_pct==null?'-':q.pass_pct}</td></tr>`).join('');
 const bad=[];Object.entries(SW.questlines).forEach(([k,q])=>q.quests.forEach(x=>{if(x.failing.length)bad.push([k,x])}));
 $('sw-bad').innerHTML='<tr><th>questline</th><th>quest</th><th>stages failing</th><th>reasons</th></tr>'+bad.map(([k,x])=>`<tr><td>${esc(k)}</td><td>${esc(x.quest)}</td><td>${esc(x.failing.map(f=>f.stage).join(', '))}</td><td>${esc([...new Set(x.failing.flatMap(f=>f.reasons))].slice(0,4).join('; '))}</td></tr>`).join('')||'<tr><td colspan=4 class="mut">none</td></tr>';
}

// ---- quests
B.forEach(b=>$('st').insertAdjacentHTML('beforeend',`<option>${b}</option>`));
['T0','T1','T2','T3','T4'].forEach(t=>$('tr').insertAdjacentHTML('beforeend',`<option>${t}</option>`));
const draw=()=>{const q=$('q').value.toLowerCase(),s=$('st').value,t=$('tr').value,so=$('so').checked;
 const rows=R.quests.filter(r=>(!so||r.story)&&(!s||r.bucket==s)&&(!t||r.tier==t)&&(!q||(r.edid+' '+(r.name||'')).toLowerCase().includes(q)));
 $('tbl').innerHTML='<tr><th>Quest</th><th>Name</th><th>Tier</th><th>Questline</th><th>Status</th></tr>'+rows.slice(0,800).map(r=>`<tr><td>${esc(r.edid)}</td><td>${esc(r.name)}</td><td>${r.tier}</td><td>${esc(r.line)}</td><td>${chip(r.bucket)}${r.failing?' <span class="mut">@'+r.failing_level+'</span>':''}</td></tr>`).join('')+(rows.length>800?`<tr><td colspan=5 class="mut">${rows.length-800} more, refine filter</td></tr>`:'')};
['q','st','tr','so'].forEach(i=>$(i).addEventListener('input',draw));draw();
</script></body></html>
"""


def render_html(rep):
    vars_ = ''.join(f'--{k}:{v};' for k, v in COLORS.items())
    data = json.dumps(rep).replace('</', '<\\/')
    return TEMPLATE % {'vars': vars_, 'data': data, 'colors': json.dumps(COLORS)}
