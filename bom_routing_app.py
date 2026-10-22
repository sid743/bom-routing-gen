#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bom_routing_app.py - lightweight web UI for bom_routing_core.py

    pip install flask openpyxl
    python bom_routing_app.py          ->  http://127.0.0.1:5003

Enter a part name, upload the Product Attribute Master, download the SAP BOM and
routing CSVs.  Every run is kept (with the master that produced it) under
./bom_routing_data, so previous parts can be re-downloaded any time.

Optional env vars: BRG_PORT (5003), BRG_HOST (127.0.0.1), BRG_DATA (data folder),
BRG_NO_BROWSER=1 (don't auto-open a browser).
"""

import json
import os
import re
import shutil
import threading
import uuid
import webbrowser
from datetime import datetime

from flask import Flask, abort, jsonify, render_template_string, request, send_file

import bom_routing_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("BRG_DATA") or os.path.join(HERE, "bom_routing_data")
HISTORY = os.path.join(DATA, "history.json")
ALLOWED = (".xlsx", ".xlsm", ".csv")
_lock = threading.Lock()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


# --------------------------------------------------------------------- store
def _load():
    try:
        with open(HISTORY, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items):
    os.makedirs(DATA, exist_ok=True)
    tmp = HISTORY + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=1)
    os.replace(tmp, HISTORY)


def _safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "part"


def _public(e):
    return {k: e[k] for k in ("id", "part", "version", "created", "source_name", "summary", "warnings")}


# --------------------------------------------------------------------- routes
@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/api/history")
def history():
    return jsonify([_public(e) for e in reversed(_load())])


@app.post("/api/generate")
def generate():
    part = (request.form.get("part") or "").strip()
    f = request.files.get("master")
    if not part:
        return jsonify(error="Enter a part name."), 400
    if not f or not f.filename:
        return jsonify(error="Choose the product attribute master file."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED:
        return jsonify(error=f"'{ext or f.filename}' isn't supported - upload an .xlsx master."), 400

    eid = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    folder = os.path.join(DATA, eid)
    os.makedirs(folder)
    src = os.path.join(folder, "master" + ext)
    f.save(src)
    try:
        res = core.convert(src)
    except ValueError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify(error=str(exc)), 400
    except Exception as exc:                                  # surface, don't crash
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify(error=f"Couldn't read this file ({type(exc).__name__}: {exc})."), 400

    with _lock:
        items = _load()
        version = 1 + sum(1 for e in items if e["part"].lower() == part.lower())
        tag = _safe(part) + (f"_v{version}" if version > 1 else "")
        core.write_csv(res.bom, os.path.join(folder, f"BOM_{tag}.csv"))
        core.write_csv(res.routing, os.path.join(folder, f"ROUTING_{tag}.csv"))
        entry = {"id": eid, "part": part, "version": version,
                 "created": datetime.now().isoformat(timespec="seconds"),
                 "source_name": os.path.basename(f.filename), "summary": res.summary,
                 "warnings": res.warnings,
                 "files": {"bom": f"BOM_{tag}.csv", "routing": f"ROUTING_{tag}.csv",
                           "source": os.path.basename(src)}}
        items.append(entry)
        _save(items)
    return jsonify(_public(entry))


@app.get("/download/<eid>/<kind>")
def download(eid, kind):
    entry = next((e for e in _load() if e["id"] == eid), None)
    if entry is None or kind not in ("bom", "routing", "source"):
        abort(404)
    path = os.path.join(DATA, eid, entry["files"][kind])
    if not os.path.isfile(path):
        abort(404)
    name = entry["source_name"] if kind == "source" else entry["files"][kind]
    return send_file(path, as_attachment=True, download_name=name)


# --------------------------------------------------------------------- page
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BOM & Routing Generator</title>
<style>
 :root{--a:#1f4e79;--a2:#2f6fb0;--ok:#1a8a4a;--err:#c0392b;--warn:#9a6700;--line:#e3e8ef;
       --ink:#1c2733;--mut:#6b7785;--bg:#f4f6f9}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
   font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:28px 18px}
 .wrap{max-width:940px;margin:0 auto;display:grid;gap:18px}
 .card{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(20,40,70,.06)}
 .hd{padding:20px 24px 14px;border-bottom:1px solid var(--line)} .hd h1{margin:0;font-size:20px}
 .hd h2{margin:0;font-size:16px} .hd p{margin:5px 0 0;color:var(--mut);font-size:13px}
 .bd{padding:18px 24px 22px}
 .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
 label.lbl{font-size:12px;font-weight:600;color:var(--mut);display:block;margin-bottom:6px;text-transform:uppercase;letter-spacing:.4px}
 input[type=text]{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:9px;font-size:14px}
 input[type=text]:focus{outline:2px solid #cfe0f3;border-color:var(--a2)}
 .file input{display:none} .btn{display:inline-block;border:1px solid var(--a);background:#fff;color:var(--a);
   padding:9px 14px;border-radius:9px;font-size:13.5px;font-weight:600;cursor:pointer;white-space:nowrap;text-decoration:none}
 .btn:hover{background:#eef4fb} .btn.sm{padding:5px 10px;font-size:12.5px}
 .fn{font-size:13px;color:var(--mut);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px}
 .fn.set{color:var(--ok)}
 .go{border:0;background:var(--a);color:#fff;padding:12px 20px;border-radius:10px;font-size:15px;font-weight:700;cursor:pointer}
 .go:hover:not(:disabled){background:var(--a2)} .go:disabled{background:#aab6c4;cursor:not-allowed}
 .grid{display:grid;grid-template-columns:1fr 1.4fr;gap:16px} @media(max-width:700px){.grid{grid-template-columns:1fr}}
 .status{margin-top:12px;font-size:13.5px;min-height:18px} .status.err{color:var(--err)}
 .spin{display:inline-block;width:14px;height:14px;border:2px solid #cfd8e3;border-top-color:var(--a);border-radius:50%;
   animation:s .7s linear infinite;vertical-align:-2px;margin-right:7px} @keyframes s{to{transform:rotate(360deg)}}
 .result{display:none;margin-top:16px;border:1px solid #cde9d8;background:#f3fbf6;border-radius:11px;padding:14px 16px}
 .result .t{font-weight:700;color:var(--ok);margin-bottom:10px}
 .meta{font-size:12.5px;color:var(--mut);margin-top:10px;line-height:1.55}
 .notes{margin:8px 0 0;padding-left:18px;font-size:12.5px;color:var(--warn)}
 table{width:100%;border-collapse:collapse;font-size:13.5px}
 th{text-align:left;font-size:11.5px;text-transform:uppercase;letter-spacing:.4px;color:var(--mut);font-weight:600;
   padding:8px 10px;border-bottom:1px solid var(--line)}
 td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
 tr.det td{background:#fafbfd;font-size:12.5px;color:var(--mut)} .pill{display:inline-block;background:var(--bg);
   border:1px solid var(--line);border-radius:20px;padding:1px 8px;font-size:11.5px;color:var(--mut)}
 .tog{cursor:pointer;color:var(--a2);font-size:12px;margin-left:6px;user-select:none}
 .empty{color:var(--mut);font-size:13.5px;padding:8px 2px}
 .dl{display:flex;gap:6px;flex-wrap:wrap}
</style></head><body><div class="wrap">

<div class="card">
 <div class="hd"><h1>BOM &amp; Routing Generator</h1>
  <p>Upload a Product Attribute Master &rarr; get the SAP BOM and routing as CSV. Works with any number of levels; headers don't need to be exact.</p></div>
 <div class="bd">
  <div class="grid">
   <div><label class="lbl" for="part">Part name</label>
    <input type="text" id="part" placeholder="e.g. 13883726" autocomplete="off"></div>
   <div><label class="lbl">Product attribute master</label>
    <div class="row"><label class="file"><input type="file" id="master" accept=".xlsx,.xlsm,.csv">
      <span class="btn" id="pick">Choose file</span></label>
     <span class="fn" id="fname">.xlsx</span></div></div>
  </div>
  <div class="row" style="margin-top:16px"><button class="go" id="go" disabled>Generate BOM &amp; Routing</button></div>
  <div class="status" id="status"></div>
  <div class="result" id="result"></div>
 </div>
</div>

<div class="card">
 <div class="hd"><div class="row" style="justify-content:space-between">
   <h2>Previous parts</h2>
   <input type="text" id="filter" placeholder="Filter by part…" style="max-width:240px;padding:7px 10px;font-size:13px"></div></div>
 <div class="bd" id="hist"><div class="empty">Loading…</div></div>
</div>

</div>
<script>
const $ = id => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === 'text' ? e.textContent = v : e.setAttribute(k, v);
  kids.forEach(k => e.append(k)); return e; };
let HIST = [];

function ready(){ $('go').disabled = !($('part').value.trim() && $('master').files.length); }
$('part').addEventListener('input', ready);
$('master').addEventListener('change', () => {
  const f = $('master').files[0];
  $('fname').textContent = f ? f.name : '.xlsx'; $('fname').className = 'fn' + (f ? ' set' : '');
  $('pick').textContent = f ? 'Change' : 'Choose file'; ready(); });

function dlButtons(e){
  return el('div', {class:'dl'},
    el('a', {class:'btn sm', href:`/download/${e.id}/bom`, text:'BOM.csv'}),
    el('a', {class:'btn sm', href:`/download/${e.id}/routing`, text:'Routing.csv'}),
    el('a', {class:'btn sm', href:`/download/${e.id}/source`, text:'Master', title:e.source_name})); }

function metaText(s){
  const lv = s.level_columns, det = Object.entries(s.detected_columns).map(([k,v]) => `${k} ← "${v}"`).join(' · ');
  return `Sheet "${s.sheet}", header row ${s.header_row} · ${lv.length} level columns ("${lv[0]}" … "${lv[lv.length-1]}"), deepest level used ${s.deepest_level} · top-level part ${s.top_level_parts.join(', ') || '–'} · ${s.materials_with_operations} materials with operations, ${s.materials_without_operations} without` + (det ? `\nDetected: ${det}` : ''); }

function notesList(w){ const ul = el('ul', {class:'notes'}); w.forEach(x => ul.append(el('li', {text:x}))); return ul; }

$('go').addEventListener('click', async () => {
  $('go').disabled = true; $('result').style.display = 'none';
  $('status').className = 'status'; $('status').innerHTML = '<span class="spin"></span>Reading master…';
  try {
    const fd = new FormData(); fd.append('part', $('part').value.trim()); fd.append('master', $('master').files[0]);
    const r = await fetch('/api/generate', {method:'POST', body:fd});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || 'Generation failed.');
    $('status').textContent = '';
    const box = $('result'); box.replaceChildren(
      el('div', {class:'t', text:`✓ ${j.part}${j.version > 1 ? ' (v' + j.version + ')' : ''} — BOM ${j.summary.bom_lines} lines · Routing ${j.summary.routing_lines} lines`}),
      dlButtons(j), el('div', {class:'meta', style:'white-space:pre-line', text:metaText(j.summary)}));
    if (j.warnings.length) box.append(notesList(j.warnings));
    box.style.display = 'block';
    loadHistory();
  } catch (err) { $('status').className = 'status err'; $('status').textContent = '⚠ ' + err.message; }
  finally { ready(); }
});

function renderHistory(){
  const q = $('filter').value.trim().toLowerCase();
  const rows = HIST.filter(e => !q || e.part.toLowerCase().includes(q) || (e.summary.top_level_parts||[]).join(' ').toLowerCase().includes(q));
  const box = $('hist');
  if (!rows.length){ box.replaceChildren(el('div', {class:'empty', text: HIST.length ? 'No parts match the filter.' : 'No parts generated yet.'})); return; }
  const tb = el('tbody');
  rows.forEach(e => {
    const d = new Date(e.created);
    const tog = el('span', {class:'tog', text:'details ▾'});
    const tr = el('tr', {},
      el('td', {}, el('b', {text:e.part}), ' ', el('span', {class:'pill', text:'v' + e.version}), tog),
      el('td', {text: d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}),
      el('td', {text: (e.summary.top_level_parts||[]).join(', ')}),
      el('td', {text: e.summary.bom_lines}), el('td', {text: e.summary.routing_lines}),
      el('td', {}, dlButtons(e)));
    const det = el('tr', {class:'det', style:'display:none'},
      el('td', {colspan:'6'}, el('div', {style:'white-space:pre-line', text:`Master: ${e.source_name}\n` + metaText(e.summary)}),
         e.warnings.length ? notesList(e.warnings) : ''));
    tog.addEventListener('click', () => { const open = det.style.display === 'none';
      det.style.display = open ? '' : 'none'; tog.textContent = open ? 'details ▴' : 'details ▾'; });
    tb.append(tr, det);
  });
  box.replaceChildren(el('table', {}, el('thead', {}, el('tr', {},
    ...['Part','Generated','Top-level part','BOM lines','Routing lines','Download'].map(h => el('th', {text:h})))), tb));
}
async function loadHistory(){ HIST = await (await fetch('/api/history')).json(); renderHistory(); }
$('filter').addEventListener('input', renderHistory);
loadHistory();
</script></body></html>"""


if __name__ == "__main__":
    host = os.environ.get("BRG_HOST", "127.0.0.1")
    port = int(os.environ.get("BRG_PORT", "5003"))
    os.makedirs(DATA, exist_ok=True)
    print(f"BOM & Routing Generator  ->  http://{host}:{port}   (data: {DATA})   Ctrl+C to stop")
    if not os.environ.get("BRG_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host=host, port=port, debug=False)
