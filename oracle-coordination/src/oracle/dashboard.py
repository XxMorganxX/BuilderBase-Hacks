"""Read-only live dashboard for the Oracle host operator (specification section 75).

Standard library only. Opens the SQLite file read-only and never creates it, so it can be started before
a demo writes its first row. Serves one HTML page with two tabs: Live polls /api/state, Replay steps through
/api/timeline (the audit ledger turned into a swimlane transcript by timeline.build_timeline).
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


def load(row):
    return json.loads(row["data"])


def read_state(path):
    path = Path(path)
    if not path.exists():
        return {"status": "waiting", "database": str(path), "message": "Waiting for the database to be created"}
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        now = datetime.now(timezone.utc)
        projects = {r["id"]: load(r) for r in db.execute("SELECT id,data FROM projects")}
        sessions = []
        for row in db.execute("SELECT data FROM sessions ORDER BY rowid"):
            s = load(row)
            heartbeat = projects.get(s["project_id"], {}).get("rules", {}).get("heartbeat_seconds", 60)
            age = (now - datetime.fromisoformat(s["last_seen"])).total_seconds()
            if s["connectivity"] != "CLOSED":
                s["connectivity"] = "DISCONNECTED" if age > heartbeat * 5 else "STALE" if age > heartbeat * 2 else "ONLINE"
            sessions.append({k: s[k] for k in ["session_id", "project_id", "human_id", "repository", "agent_type", "task", "work_state", "connectivity", "last_seen"]})
        facts = {r["id"]: load(r) for r in db.execute("SELECT id,data FROM facts")}

        def evidence(ids):
            return [
                {k: facts[i][k] for k in ["subject", "predicate", "value", "source", "confidence", "project_id"]}
                for i in ids
                if i in facts
            ]

        questions = []
        for row in db.execute("SELECT data FROM questions ORDER BY rowid"):
            q = load(row)
            questions.append(
                {
                    **{k: q[k] for k in ["question_id", "project_id", "subject", "predicate", "question", "status", "decision_id", "created_at"]},
                    "affected": len(q["affected_sessions"]),
                    "positions": evidence(q["fact_ids"]),
                    "suggestion": q.get("suggestion"),
                    "assessment": q.get("assessment"),
                }
            )
        conflicts = []
        for row in db.execute("SELECT data FROM conflicts ORDER BY rowid"):
            c = load(row)
            conflicts.append(
                {
                    **{k: c[k] for k in ["conflict_id", "project_id", "subject", "predicate", "type", "state", "round", "decision_id", "created_at"]},
                    "affected": c["affected_sessions"],
                    "pause_acks": len(c["pause_acks"]),
                    "positions": evidence(c["fact_ids"]),
                    "proposal": c.get("proposal"),
                    "responses": {k: v["position"] for k, v in c.get("responses", {}).items()},
                    "suggested_proposal": c.get("suggested_proposal"),
                    "assessment": c.get("assessment"),
                }
            )
        decisions = [
            {k: d[k] for k in ["decision_id", "project_id", "subject", "predicate", "value", "authority", "author_id", "reason", "visibility", "timestamp"]}
            | {"affected": len(d["affected_sessions"]), "acknowledged": len(d["acknowledgements"])}
            for d in (load(r) for r in db.execute("SELECT data FROM decisions ORDER BY rowid"))
        ]
        jobs = []
        for row in db.execute("SELECT id,session_id,subject,state,attempts,data,result FROM reasoning_jobs ORDER BY rowid DESC LIMIT 40"):
            meta = json.loads(row["data"])
            result = json.loads(row["result"]) if row["result"] else {}
            jobs.append(
                {
                    "job_id": row["id"],
                    "task": meta.get("task", "conflict-detector"),
                    "subject": row["subject"],
                    "state": row["state"],
                    "attempts": row["attempts"],
                    "seconds": (result.get("metrics") or {}).get("seconds"),
                    "model": (result.get("metrics") or {}).get("model"),
                    "error": result.get("error"),
                    "classification": (result.get("output") or {}).get("classification"),
                }
            )
        audit = [
            {k: r[k] for k in ["seq", "actor", "action", "object_id", "timestamp"]}
            for r in db.execute("SELECT seq,actor,action,object_id,timestamp FROM audit ORDER BY seq DESC LIMIT 60")
        ]
        pending = [load(r) for r in db.execute("SELECT data FROM messages WHERE acknowledged_at IS NULL ORDER BY seq DESC LIMIT 30")]
        active_facts = [
            {k: f[k] for k in ["subject", "predicate", "value", "source", "confidence", "project_id", "visibility", "lifecycle"]}
            for f in facts.values()
            if f["lifecycle"] in {"ACTIVE", "PROPOSED", "DISPUTED"}
        ]
        active_facts.sort(key=lambda f: (f["subject"], f["predicate"], f["source"]))
        return {
            "status": "ok",
            "database": str(path),
            "generated_at": now.isoformat(),
            "projects": [{"project_id": k, "owner_id": v["owner_id"], "rules": v["rules"]} for k, v in projects.items()],
            "sessions": sessions,
            "questions": questions,
            "conflicts": conflicts,
            "decisions": decisions,
            "jobs": jobs,
            "audit": audit,
            "pending_commands": [{"type": m["type"], "session_id": m["session_id"], "timestamp": m["timestamp"]} for m in pending],
            "facts": active_facts,
            "summary": {
                "sessions": len(sessions),
                "humans": len({s["human_id"] for s in sessions}),
                "running": sum(s["work_state"] == "RUNNING" for s in sessions),
                "paused": sum(s["work_state"] != "RUNNING" for s in sessions),
                "online": sum(s["connectivity"] == "ONLINE" for s in sessions),
                "open_questions": sum(q["status"] == "OPEN" for q in questions),
                "open_conflicts": sum(c["state"] != "CLOSED" for c in conflicts),
                "decisions": len(decisions),
                "model_runs": sum(j["state"] in {"COMPLETE", "STALE"} for j in jobs),
                "model_failures": sum(j["state"] == "FAILED" for j in jobs),
                "model_seconds": round(sum(j["seconds"] or 0 for j in jobs), 1),
            },
        }
    finally:
        db.close()


def read_timeline(path):
    """Replay data. The import is deferred so a checkout without timeline.py still serves the Live tab."""
    try:
        from .timeline import build_timeline
    except ImportError:
        return {"status": "unavailable", "database": str(path), "message": "timeline module not available"}
    return build_timeline(path)


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ORACLE</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0e1116;--card:#161b22;--line:#2a313c;--fg:#e6e9ef;--dim:#8b96a8;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--ai:#a371f7;--hum:#58a6ff;--raise:#1f2630}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;padding:18px}
h1{font-size:18px;margin:0 0 4px;letter-spacing:.08em}h2{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.12em;margin:0 0 10px}
.top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:14px}.dim{color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;min-width:0}
.wide{grid-column:1/-1}.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:14px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px}.stat b{display:block;font-size:22px}.stat span{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.08em}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top;word-break:break-word}th{color:var(--dim);font-weight:normal;font-size:11px;text-transform:uppercase;letter-spacing:.08em}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:11px;border:1px solid var(--line)}.RUNNING,.ONLINE,.CLOSED,.ANSWERED,.COMPLETE,.ALLOW{color:var(--ok);border-color:var(--ok)}
.PAUSE_REQUESTED,.STALE,.NEGOTIATION,.PAUSED,.RESOLUTION_PENDING,.PENDING,.RUNNING_JOB,.HUMAN_ESCALATION{color:var(--warn);border-color:var(--warn)}.DISCONNECTED,.FAILED,.OPEN,.CONFLICTING{color:var(--bad);border-color:var(--bad)}
.ai{color:var(--ai)}.hum{color:var(--hum)}.val{color:#f0c674}.small{font-size:12px}.muted{opacity:.7}ul{margin:4px 0;padding-left:18px}li{margin:2px 0}
.empty{color:var(--dim);font-style:italic}.pulse{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px;animation:p 1.4s infinite}@keyframes p{50%{opacity:.25}}
.timeline li{list-style:none;display:grid;grid-template-columns:70px 110px 1fr;gap:8px}.timeline{padding:0;max-height:360px;overflow:auto}
[hidden]{display:none!important}
.tabs{display:flex;gap:6px;margin-bottom:14px}.tab{background:var(--card);border:1px solid var(--line);color:var(--dim);padding:6px 18px;border-radius:6px;cursor:pointer;font:inherit;font-size:12px;letter-spacing:.1em;text-transform:uppercase}.tab.on{color:var(--fg);border-color:var(--hum)}.tab:hover{color:var(--fg)}
.rp-bar{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.rp-bar label{display:flex;align-items:center;gap:5px;color:var(--dim);font-size:12px;white-space:nowrap}
.btn{background:var(--raise);border:1px solid var(--line);color:var(--fg);padding:4px 10px;border-radius:6px;cursor:pointer;font:inherit}.btn:hover{border-color:var(--hum)}.btn:disabled{opacity:.4;cursor:default}
input[type=range]{accent-color:var(--hum)}input[type=checkbox]{accent-color:var(--hum)}
.lk-human{color:var(--hum)}.lk-owner{color:var(--warn)}.lk-oracle{color:var(--dim)}.lk-model{color:var(--ai)}
.chip{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;border:1px solid currentColor}
.kind{display:inline-block;padding:1px 7px;border-radius:4px;font-size:11px;background:var(--raise);color:var(--dim);text-transform:uppercase;letter-spacing:.06em;margin-left:6px}
.lanes{display:flex;min-width:0}.lanes .names{flex:0 0 130px}.lanes .names div{height:24px;line-height:24px;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-right:8px}
.lanes .strip{flex:1;min-width:0;overflow-x:auto;overflow-y:hidden}.lanes svg{display:block}.lanes .dot{cursor:pointer;stroke:transparent;stroke-width:6}.lanes .dot.on{stroke:#fff;stroke-width:1.5}
.dot.human{fill:var(--hum)}.dot.owner{fill:var(--warn)}.dot.oracle{fill:var(--dim)}.dot.model{fill:var(--ai)}
.conn{stroke:var(--line);stroke-dasharray:2 3}.conn.cmd{stroke:var(--dim);stroke-dasharray:none;stroke-width:1.5}.tgt{fill:var(--dim)}#rp-cursor{stroke:var(--hum);stroke-opacity:.55;stroke-width:1.5}
.rp-main{display:grid;grid-template-columns:minmax(0,2fr) minmax(280px,1fr);gap:14px;margin-bottom:14px}@media(max-width:900px){.rp-main{grid-template-columns:1fr}}
.rp-title{font-size:20px;line-height:1.3;margin:10px 0;word-break:break-word}.rp-detail{white-space:pre-wrap;word-break:break-word;opacity:.85;margin:0 0 10px;font:inherit}
details{border:1px solid var(--line);border-radius:6px;padding:6px 10px;margin-top:8px;min-width:0}summary{cursor:pointer;color:var(--dim);font-size:12px;text-transform:uppercase;letter-spacing:.08em}details pre{white-space:pre-wrap;word-break:break-word;font-size:12px;max-height:340px;overflow:auto;margin:8px 0 0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media(max-width:700px){.two{grid-template-columns:1fr}}
.steplist{position:relative;max-height:300px;overflow:auto}.steplist .row{display:grid;grid-template-columns:40px 64px 200px 1fr;gap:8px;padding:3px 6px;border-bottom:1px solid var(--line);cursor:pointer;font-size:12px}.steplist .row:hover{background:var(--raise)}.steplist .row.on{background:#1d2a3a;box-shadow:inset 2px 0 0 var(--hum)}
.steplist .row span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.mini td{padding:3px 6px}.refs span{margin-right:10px}
</style></head><body>
<div class="top"><div><h1>ORACLE</h1><div class="dim" id="db"></div></div><div class="dim"><span class="pulse"></span><span id="ts">connecting…</span></div></div>
<div class="tabs"><button class="tab on" data-tab="live">Live</button><button class="tab" data-tab="replay">Replay</button></div>
<div id="live">
<div class="stats" id="stats"></div>
<div class="grid">
<div class="card wide"><h2>Sessions</h2><div id="sessions"></div></div>
<div class="card"><h2>Specification questions</h2><div id="questions"></div></div>
<div class="card"><h2>Conflicts &amp; mediation</h2><div id="conflicts"></div></div>
<div class="card"><h2>Decisions</h2><div id="decisions"></div></div>
<div class="card"><h2>Local model activity</h2><div id="jobs"></div></div>
<div class="card"><h2>Living contracts (active facts)</h2><div id="facts"></div></div>
<div class="card"><h2>Audit timeline</h2><ul class="timeline" id="audit"></ul></div>
</div>
</div>
<div id="replay" hidden>
<div class="card" style="margin-bottom:14px"><div class="rp-bar">
<button class="btn" id="rp-first" title="first step">|◀</button><button class="btn" id="rp-prev" title="previous step (←)">◀</button><button class="btn" id="rp-play" title="play / pause (space)">▶ play</button><button class="btn" id="rp-next" title="next step (→)">▶</button><button class="btn" id="rp-last" title="last step">▶|</button>
<input type="range" id="rp-range" min="1" max="1" value="1" style="flex:1;min-width:160px">
<b id="rp-pos">—</b>
<label>speed <input type="range" id="rp-speed" min="500" max="4000" step="250" value="1500" style="width:90px"> <span id="rp-speedv">1.5s</span></label>
<label><input type="checkbox" id="rp-hb" checked> hide heartbeats</label>
<label><input type="checkbox" id="rp-follow"> follow live</label>
<span class="dim small" id="rp-status"></span>
</div></div>
<div class="card" style="margin-bottom:14px"><h2>Swimlanes</h2><div class="lanes"><div class="names" id="rp-names"></div><div class="strip" id="rp-strip"></div></div></div>
<div class="rp-main"><div class="card" id="rp-card"></div><div class="card" id="rp-state"></div></div>
<div class="card"><h2>Steps</h2><div class="steplist" id="rp-list"></div></div>
</div>
<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const j=v=>esc(JSON.stringify(v));const tag=s=>`<span class="tag ${esc(s)}">${esc(s)}</span>`;
const src=s=>`<span class="${/human|spec|architecture/.test(s)?'hum':/oracle/.test(s)?'ai':'dim'}">${esc(s)}</span>`;
const t=s=>esc((s||'').replace('T',' ').slice(11,19));
function stat(n,l){return `<div class="stat"><b>${n}</b><span>${l}</span></div>`}
function positions(p){return p.length?`<ul class="small">${p.map(f=>`<li><span class="val">${j(f.value)}</span> <span class="dim">${esc(f.project_id)}</span> ${src(f.source)} <span class="dim">${f.confidence}</span></li>`).join('')}</ul>`:''}
function render(s){
 document.getElementById('db').textContent=s.database;
 if(s.status!=='ok'){document.getElementById('ts').textContent=s.message;return}
 document.getElementById('ts').textContent='live · '+t(s.generated_at)+' UTC';
 const m=s.summary;
 document.getElementById('stats').innerHTML=stat(m.humans,'humans')+stat(m.sessions,'sessions')+stat(m.running,'running')+stat(m.paused,'paused')+stat(m.open_questions,'open questions')+stat(m.open_conflicts,'open conflicts')+stat(m.decisions,'decisions')+stat(m.model_runs,'model runs')+stat(m.model_seconds+'s','model time');
 document.getElementById('sessions').innerHTML=s.sessions.length?`<table><tr><th>human</th><th>project</th><th>agent</th><th>task</th><th>work</th><th>link</th><th>last seen</th></tr>${s.sessions.map(x=>`<tr><td>${esc(x.human_id)}</td><td>${esc(x.project_id)}</td><td class="dim">${esc(x.agent_type)}</td><td class="small">${esc(x.task)}</td><td>${tag(x.work_state)}</td><td>${tag(x.connectivity)}</td><td class="dim">${t(x.last_seen)}</td></tr>`).join('')}</table>`:'<div class="empty">no sessions registered</div>';
 document.getElementById('questions').innerHTML=s.questions.length?s.questions.slice().reverse().map(q=>`<div style="margin-bottom:12px"><div>${tag(q.status)} <b>${esc(q.subject)}.${esc(q.predicate)}</b> <span class="dim small">${esc(q.project_id)} · ${q.affected} sessions</span></div><div class="small muted">${esc(q.question)}</div>${positions(q.positions)}${q.suggestion?`<div class="small ai">✦ ${esc(q.suggestion.question)}<br>${(q.suggestion.options||[]).map((o,i)=>`[${i+1}] ${esc(o)}${o===q.suggestion.recommendation?' ← recommended':''}`).join(' &nbsp; ')}${q.suggestion.why_it_matters?`<div class="dim">${esc(q.suggestion.why_it_matters)}</div>`:''}</div>`:''}${q.assessment?`<div class="small dim">model: ${esc(q.assessment.classification)} ${q.assessment.confidence}</div>`:''}${q.decision_id?`<div class="small hum">→ decided ${esc(q.decision_id.slice(0,12))}</div>`:''}</div>`).join(''):'<div class="empty">none</div>';
 document.getElementById('conflicts').innerHTML=s.conflicts.length?s.conflicts.slice().reverse().map(c=>`<div style="margin-bottom:12px"><div>${tag(c.state)} <b>${esc(c.subject)}.${esc(c.predicate)}</b> <span class="dim small">${esc(c.type)} · round ${c.round} · ${c.pause_acks}/${c.affected.length} paused</span></div>${positions(c.positions)}${c.assessment?`<div class="small dim">model reading: ${esc(c.assessment.classification)} ${c.assessment.confidence} — ${esc(c.assessment.summary)}</div>`:''}${c.suggested_proposal?`<div class="small ai">✦ proposes <span class="val">${j(c.suggested_proposal.value)}</span> ${c.suggested_proposal.requires_human?'(needs a human)':'(low-risk, may self-propose)'}<div class="dim">${esc(c.suggested_proposal.reason)}</div>${Object.entries(c.suggested_proposal.participant_changes||{}).map(([k,v])=>`<div class="dim">· ${esc(k)}: ${esc(v)}</div>`).join('')}</div>`:''}${c.proposal?`<div class="small">round ${c.round} by <span class="${c.proposal.actor==='oracle-model'?'ai':'hum'}">${esc(c.proposal.actor)}</span>: <span class="val">${j(c.proposal.value)}</span> · responses: ${Object.values(c.responses).map(esc).join(', ')||'—'}</div>`:''}${c.decision_id?`<div class="small hum">→ decided ${esc(c.decision_id.slice(0,12))}</div>`:''}</div>`).join(''):'<div class="empty">none</div>';
 document.getElementById('decisions').innerHTML=s.decisions.length?`<table>${s.decisions.slice().reverse().map(d=>`<tr><td><b>${esc(d.subject)}.${esc(d.predicate)}</b><div class="small dim">${esc(d.reason)}</div></td><td class="val">${j(d.value)}</td><td>${src(d.authority)}<div class="small dim">${d.acknowledged}/${d.affected} ack</div></td></tr>`).join('')}</table>`:'<div class="empty">none yet</div>';
 document.getElementById('jobs').innerHTML=s.jobs.length?`<table><tr><th>skill</th><th>subject</th><th>state</th><th>result</th></tr>${s.jobs.map(x=>`<tr><td class="ai">${esc(x.task)}</td><td class="small">${esc(x.subject)}</td><td>${tag(x.state==='RUNNING'?'RUNNING_JOB':x.state)}</td><td class="small dim">${x.classification?esc(x.classification)+' · ':''}${x.seconds?x.seconds.toFixed(1)+'s':''}${x.error?'<span style="color:var(--bad)">'+esc(x.error)+'</span>':''}</td></tr>`).join('')}</table>`:'<div class="empty">no model work queued</div>';
 document.getElementById('facts').innerHTML=s.facts.length?`<table>${s.facts.map(f=>`<tr><td><b>${esc(f.subject)}</b>.${esc(f.predicate)}</td><td class="val">${j(f.value)}</td><td>${src(f.source)}</td><td class="small dim">${esc(f.project_id)} · ${esc(f.visibility)}${f.lifecycle!=='ACTIVE'?' · '+esc(f.lifecycle):''}</td></tr>`).join('')}</table>`:'<div class="empty">none</div>';
 document.getElementById('audit').innerHTML=s.audit.map(a=>`<li><span class="dim">${t(a.timestamp)}</span><span class="${a.actor==='oracle-model'?'ai':a.actor==='oracle'?'dim':'hum'}">${esc(a.actor)}</span><span>${esc(a.action)} <span class="dim">${esc(a.object_id.slice(0,18))}</span></span></li>`).join('');
}
async function tick(){try{const r=await fetch('/api/state',{cache:'no-store'});render(await r.json())}catch(e){document.getElementById('ts').textContent='disconnected'}}
tick();setInterval(tick,1500);

/* ---- Replay: step-through viewer over /api/timeline ---- */
const $=id=>document.getElementById(id);
const R={tl:null,steps:[],idx:0,kinds:{},msg:'loading…',hideHb:true,follow:false,playing:false,speed:1500,ptimer:null,ftimer:null,busy:false,tab:'live'};
const KINDS=['human','owner','oracle','model'];
const kindOf=n=>KINDS.includes(R.kinds[n])?R.kinds[n]:n==='model'?'model':n==='oracle'?'oracle':'human';
const lk=n=>`<span class="lk-${kindOf(n)}">${esc(n)}</span>`;const chip=n=>`<span class="chip lk-${kindOf(n)}">${esc(n)}</span>`;
const pretty=v=>{try{return esc(JSON.stringify(v??null,null,2))}catch(e){return esc(String(v))}};
const cur=()=>R.steps[R.idx];
function showTab(n){
 R.tab=n;document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===n));
 $('live').hidden=n!=='live';$('replay').hidden=n!=='replay';
 if(n==='replay'){if(!R.tl)rpFetch();else{renderLanes();rpCursor()}}
 try{history.replaceState(null,'','#'+n)}catch(e){}
}
async function rpFetch(){
 if(R.busy)return;R.busy=true;
 try{const r=await fetch('/api/timeline',{cache:'no-store'});rpLoad(await r.json())}catch(e){R.msg='dashboard unreachable';$('rp-status').textContent=R.msg;if(!R.tl)rpApply(null,false)}finally{R.busy=false}
}
function rpLoad(tl){
 tl=tl&&typeof tl==='object'?tl:{status:'error',message:'bad response'};
 const keep=(cur()||{}).seq;
 R.tl=tl;R.kinds={};(tl.participants||[]).forEach(p=>{if(p&&p.lane)R.kinds[p.lane]=p.kind});
 R.msg=tl.status==='waiting'?'waiting for database':tl.status==='unavailable'?'timeline module unavailable':tl.status==='error'?'error: '+(tl.message||'unknown'):'no steps yet';
 if(tl.database)$('db').textContent=tl.database;
 rpApply(keep,R.follow);
 const all=(tl.steps||[]).length,hb=all-R.steps.length;
 $('rp-status').textContent=(tl.status==='ok'?`${all} steps${hb?` (${hb} heartbeats hidden)`:''} · ${(tl.participants||[]).length} lanes`:R.msg)+(R.follow?' · following live':'');
}
function rpApply(keepSeq,jumpEnd){
 R.steps=((R.tl&&R.tl.steps)||[]).filter(s=>s&&!(R.hideHb&&s.kind==='heartbeat'));
 const N=R.steps.length;
 if(jumpEnd||!N)R.idx=Math.max(0,N-1);
 else{let i=keepSeq==null?-1:R.steps.findIndex(s=>s.seq>=keepSeq);if(i<0)i=Math.min(R.idx,N-1);R.idx=Math.max(0,i)}
 renderLanes();renderList();rpCursor();
}
function renderLanes(){
 const P=(R.tl&&R.tl.participants||[]).filter(p=>p&&p.lane),V=R.steps,N=V.length,rowH=24,strip=$('rp-strip');
 $('rp-names').innerHTML=P.map(p=>`<div class="lk-${kindOf(p.lane)}" title="${esc(p.kind)}${p.project?' · '+esc(p.project):''}">${esc(p.lane)}${p.project?` <span class="dim">${esc(p.project)}</span>`:''}</div>`).join('')||`<div class="empty">no participants</div>`;
 if(!P.length){strip.innerHTML='';return}
 const avail=Math.max(200,(strip.clientWidth||600)-8),px=Math.min(24,Math.max(7,Math.floor(avail/Math.max(N,1))));
 const W=Math.max(avail,N*px+px),H=P.length*rowH,rowOf=n=>{const i=P.findIndex(p=>p.lane===n);return i<0?null:i},y=r=>r*rowH+rowH/2;
 let g=P.map((p,i)=>`<line x1="0" x2="${W}" y1="${y(i)}" y2="${y(i)}" stroke="var(--line)"/>`).join('');
 V.forEach((s,i)=>{
  const x=i*px+px/2,r1=rowOf(s.lane);if(r1===null)return;
  const r2=s.target?rowOf(s.target):null;
  if(r2!==null&&r2!==r1)g+=`<line class="conn${s.kind==='command'?' cmd':''}" x1="${x}" x2="${x}" y1="${y(r1)}" y2="${y(r2)}"/><circle class="tgt" cx="${x}" cy="${y(r2)}" r="${s.kind==='command'?2.5:1.5}"/>`;
  g+=`<circle class="dot ${kindOf(s.lane)}" data-i="${i}" cx="${x}" cy="${y(r1)}" r="3"><title>${i+1} · ${esc(s.kind)} · ${esc(s.title)}</title></circle>`;
 });
 g+=`<line id="rp-cursor" x1="0" x2="0" y1="0" y2="${H}"/>`;
 strip.innerHTML=`<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${g}</svg>`;
}
function renderList(){
 $('rp-list').innerHTML=R.steps.map((s,i)=>`<div class="row" data-i="${i}"><span class="dim">${i+1}</span><span class="dim">${t(s.timestamp)}</span><span>${lk(s.lane)}${s.target?` <span class="dim">→</span> ${lk(s.target)}`:''}</span><span title="${esc(s.title)}">${esc(s.title)}</span></div>`).join('')||`<div class="empty">${esc(R.msg)}</div>`;
}
function rpCursor(){
 const N=R.steps.length,i=R.idx;
 document.querySelectorAll('#rp-strip .dot').forEach(c=>{const on=+c.dataset.i===i;c.classList.toggle('on',on);c.setAttribute('r',on?5:3)});
 const line=$('rp-cursor'),dot=document.querySelector(`#rp-strip .dot[data-i="${i}"]`),strip=$('rp-strip');
 if(line&&dot){const x=+dot.getAttribute('cx');line.setAttribute('x1',x);line.setAttribute('x2',x);line.style.display='';
  if(x<strip.scrollLeft+16||x>strip.scrollLeft+strip.clientWidth-16)strip.scrollLeft=Math.max(0,x-strip.clientWidth/2)}
 else if(line)line.style.display='none';
 const list=$('rp-list');document.querySelectorAll('#rp-list .row').forEach(r=>r.classList.toggle('on',+r.dataset.i===i));
 const row=list.querySelector(`.row[data-i="${i}"]`);if(row){const top=row.offsetTop,h=row.offsetHeight;if(top<list.scrollTop||top+h>list.scrollTop+list.clientHeight)list.scrollTop=Math.max(0,top-list.clientHeight/2+h)}
 const rg=$('rp-range');rg.max=Math.max(N,1);rg.value=N?i+1:1;rg.disabled=!N;
 $('rp-pos').textContent=N?`step ${i+1} / ${N}`:'—';
 $('rp-first').disabled=$('rp-prev').disabled=!N||i<=0;$('rp-next').disabled=$('rp-last').disabled=!N||i>=N-1;
 renderCard();renderState();
}
function renderCard(){
 const s=cur(),el=$('rp-card');
 if(!s){el.innerHTML=`<h2>Current step</h2><div class="empty">${esc(R.msg)}</div>`;return}
 const refs=Object.entries(s.refs||{}).filter(([k,v])=>v).map(([k,v])=>`<span class="dim">${esc(k)} <span class="val">${esc(String(v).slice(0,24))}</span></span>`).join('');
 let body;
 if(s.kind==='model'||s.kind==='model_failed'){
  const p=s.payload||{},m=p.metrics||{},sec=Number(m.seconds);
  body=`<div class="small ai" style="margin:8px 0 2px">${m.model?'model '+esc(m.model):'model'}${isFinite(sec)&&m.seconds!=null?` · ${sec.toFixed(1)}s`:''}${m.usage?` · <span class="dim">${esc(JSON.stringify(m.usage))}</span>`:''}${p.error?` · <span style="color:var(--bad)">${esc(p.error)}</span>`:''}</div>
  <div class="two"><details open><summary>evidence sent to the model</summary><pre>${pretty(p.bundle)}</pre></details><details open><summary>validated output</summary><pre>${pretty(p.output)}</pre></details></div>`;
 }else body=`<details><summary>payload</summary><pre>${pretty(s.payload)}</pre></details>`;
 el.innerHTML=`<div class="dim small">step ${R.idx+1} / ${R.steps.length} · seq ${esc(s.seq)} · ${esc((s.timestamp||'').replace('T',' ').slice(0,19))} UTC</div>
 <div style="margin-top:8px">${chip(s.lane)}${s.target?` <span class="dim">→</span> ${chip(s.target)}`:''}<span class="kind">${esc(s.kind)}</span></div>
 <div class="rp-title">${esc(s.title)}</div>${s.detail?`<pre class="rp-detail">${esc(s.detail)}</pre>`:''}${refs?`<div class="small refs">${refs}</div>`:''}${body}`;
}
function renderState(){
 const s=cur(),st=(s&&s.state)||{},ses=Object.entries(st.sessions||{}),oq=st.open_questions||[],oc=st.open_conflicts||[],el=$('rp-state');
 const ids=(a,c)=>a.length?a.map(x=>`<span class="tag ${c}" title="${esc(x)}">${esc(String(x).slice(0,16))}</span>`).join(' '):'<span class="empty">none</span>';
 el.innerHTML=`<h2>World state after step ${s?R.idx+1:'—'}</h2>`+(s?(ses.length?`<table class="mini"><tr><th>human</th><th>project</th><th>work</th></tr>${ses.map(([h,v])=>`<tr><td>${lk(h)}</td><td class="dim">${esc((v||{}).project)}</td><td>${tag((v||{}).work_state||'UNKNOWN')}</td></tr>`).join('')}</table>`:'<div class="empty">no sessions yet</div>')
 +`<div class="stats" style="grid-template-columns:1fr 1fr;margin:12px 0 0">${stat(st.decisions||0,'decisions')}${stat(st.model_runs||0,'model runs')}</div>
 <div class="small" style="margin-top:12px"><div class="dim">open questions (${oq.length})</div>${ids(oq,'OPEN')}</div>
 <div class="small" style="margin-top:8px"><div class="dim">open conflicts (${oc.length})</div>${ids(oc,'CONFLICTING')}</div>`:`<div class="empty">${esc(R.msg)}</div>`);
}
function rpGo(i){const N=R.steps.length;if(!N)return;R.idx=Math.max(0,Math.min(N-1,i));rpCursor()}
function rpPlay(on){
 R.playing=on;clearInterval(R.ptimer);R.ptimer=null;$('rp-play').textContent=on?'❚❚ pause':'▶ play';
 if(on)R.ptimer=setInterval(()=>{if(R.idx<R.steps.length-1)rpGo(R.idx+1);else if(!R.follow)rpPlay(false)},R.speed);
}
function rpFollow(on){R.follow=on;clearInterval(R.ftimer);R.ftimer=null;if(on){rpFetch();R.ftimer=setInterval(rpFetch,2000)}else if(R.tl)rpLoad(R.tl)}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
$('rp-first').onclick=()=>rpGo(0);$('rp-prev').onclick=()=>rpGo(R.idx-1);$('rp-next').onclick=()=>rpGo(R.idx+1);$('rp-last').onclick=()=>rpGo(R.steps.length-1);
$('rp-play').onclick=()=>rpPlay(!R.playing);
$('rp-range').oninput=e=>rpGo(+e.target.value-1);
$('rp-speed').oninput=e=>{R.speed=+e.target.value||1500;$('rp-speedv').textContent=(R.speed/1000).toFixed(2).replace(/\.?0+$/,'')+'s';if(R.playing)rpPlay(true)};
$('rp-hb').onchange=e=>{R.hideHb=e.target.checked;if(R.tl)rpLoad(R.tl)};
$('rp-follow').onchange=e=>rpFollow(e.target.checked);
$('rp-strip').onclick=e=>{const c=e.target.closest&&e.target.closest('.dot');if(c)rpGo(+c.dataset.i)};
$('rp-list').onclick=e=>{const r=e.target.closest&&e.target.closest('.row');if(r)rpGo(+r.dataset.i)};
document.addEventListener('keydown',e=>{
 if(R.tab!=='replay')return;const tg=(e.target.tagName||'').toUpperCase();if(['INPUT','SELECT','TEXTAREA'].includes(tg))return;
 if(e.key==='ArrowLeft'){e.preventDefault();rpGo(R.idx-1)}else if(e.key==='ArrowRight'){e.preventDefault();rpGo(R.idx+1)}
 else if(e.key===' '&&tg!=='BUTTON'){e.preventDefault();rpPlay(!R.playing)}
 else if(e.key==='Home'){e.preventDefault();rpGo(0)}else if(e.key==='End'){e.preventDefault();rpGo(R.steps.length-1)}
});
window.addEventListener('resize',()=>{if(R.tab==='replay'){renderLanes();rpCursor()}});
showTab(location.hash==='#replay'?'replay':'live');
</script></body></html>
"""


def serve(db, host="127.0.0.1", port=8765):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            route = urlparse(self.path).path
            if route == "/api/state":
                try:
                    body = json.dumps(read_state(db), default=str).encode()
                except sqlite3.Error as error:
                    body = json.dumps({"status": "error", "database": str(db), "message": str(error)}).encode()
                kind = "application/json"
            elif route == "/api/timeline":
                try:
                    body = json.dumps(read_timeline(db), default=str).encode()
                except Exception as error:  # a replay fault must never take the live view down with it
                    body = json.dumps({"status": "error", "database": str(db), "message": str(error)}).encode()
                kind = "application/json"
            elif route == "/":
                body, kind = PAGE.encode(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"ORACLE dashboard on http://{host}:{port}/  (database {db}, read-only)", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
