"""Build a self-contained offline HTML presenter (no server required)."""
from __future__ import annotations
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any, List, Optional


def _file_to_data_uri(path: Optional[str], base: Path) -> str:
    if not path:
        return ""
    p = str(path).strip()
    if p.startswith("data:"):
        return p
    if p.startswith("http://") or p.startswith("https://"):
        return p
    if p.startswith("/static/"):
        rel = p[len("/static/"):]
        candidates = [base / "app" / "static" / rel, base / "static" / rel, Path("app/static") / rel]
    else:
        candidates = [Path(p)]
    for c in candidates:
        if c.exists() and c.is_file():
            mime = mimetypes.guess_type(str(c))[0] or "application/octet-stream"
            data = base64.b64encode(c.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{data}"
    return p


def slide_dict(s: Any, base: Path) -> dict:
    images = []
    raw_json = getattr(s, "images_json", None)
    if raw_json:
        try:
            arr = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(arr, list):
                images.extend(arr)
        except Exception:
            pass
    img = getattr(s, "image_path", None)
    online = getattr(s, "online_image_url", None)
    if img:
        images.insert(0, img)
    if online:
        images.append(online)
    seen, uniq = set(), []
    for u in images:
        if u and u not in seen:
            seen.add(u)
            if str(u).startswith("http"):
                uniq.append(u)
            else:
                uniq.append(_file_to_data_uri(u, base))
    return {
        "title": getattr(s, "title", "") or "",
        "body": getattr(s, "body", "") or "",
        "bg": getattr(s, "bg_color", None) or "#0f172a",
        "accent": getattr(s, "accent", None) or "#14b8a6",
        "layout": getattr(s, "layout_style", None) or "title_body",
        "chartType": getattr(s, "chart_type", None) or "",
        "chartData": getattr(s, "chart_data", None) or "",
        "chartEffect": getattr(s, "chart_effect", None) or "grow",
        "chartLabelMode": getattr(s, "chart_label_mode", None) or "outside",
        "showDataTable": bool(getattr(s, "show_data_table", False)),
        "fontFamily": getattr(s, "font_family", None) or "Inter",
        "fontColor": getattr(s, "font_color", None) or "#e2e8f0",
        "images": uniq,
        "image": uniq[0] if uniq else "",
    }


def build_offline_html(title: str, slides: List[Any], base: Path) -> bytes:
    data = [slide_dict(s, base) for s in slides]
    payload = json.dumps({"title": title, "slides": data}, ensure_ascii=False)
    payload_js = payload.replace("</", "<\\/")
    # HTML template uses replace tokens
    html = _HTML_TEMPLATE.replace("__TITLE__", (title or "Eleon").replace("<", ""))
    html = html.replace("__PAYLOAD__", payload_js)
    return html.encode("utf-8")


_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - Offline Eleon</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-plugin-datalabels@2.2.0/dist/chartjs-plugin-datalabels.min.js"></script>
<style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:#020617;color:#e2e8f0}
header{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;padding:.75rem 1rem;border-bottom:1px solid rgba(255,255,255,.1);background:#0f172a}
header button{border:0;border-radius:.75rem;padding:.4rem .9rem;font-weight:700;cursor:pointer;color:#fff;background:#334155}
.btn-teal{background:#0d9488!important}.btn-amber{background:#b45309!important}
#stage{min-height:calc(100vh - 3.5rem);display:flex;align-items:center;justify-content:center;padding:1.5rem}
.card{max-width:56rem;width:100%;border-radius:1.25rem;padding:2rem;background:rgba(15,23,42,.75);box-shadow:0 25px 80px rgba(0,0,0,.45)}
h1{margin:0 0 1rem;font-size:clamp(1.5rem,4vw,2.4rem);font-weight:900}
.body{white-space:pre-wrap;line-height:1.55;font-size:1.1rem}
img.photo{max-height:280px;max-width:100%;border-radius:1rem;object-fit:cover;margin-top:1rem}
.multi{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:1rem}.multi img{height:100px;border-radius:.75rem;object-fit:cover}
.chart-wrap{height:240px;margin-top:1rem}
table.dt{width:100%;margin-top:1rem;border-collapse:collapse}table.dt td,table.dt th{border-bottom:1px solid rgba(255,255,255,.1);padding:.4rem;text-align:left}
.race-row{display:grid;grid-template-columns:7rem 1fr 3rem;gap:.5rem;align-items:center;margin:.35rem 0}
.race-track{background:rgba(255,255,255,.08);border-radius:999px;height:1rem;overflow:hidden}
.race-bar{height:100%;width:0;background:linear-gradient(90deg,#14b8a6,#6366f1);border-radius:999px;transition:width 1.2s ease-out}
.brand{position:fixed;bottom:.5rem;right:1rem;font-size:.7rem;color:#64748b}
</style></head><body>
<header>
<button type="button" onclick="prev()">Prev</button>
<button type="button" class="btn-teal" onclick="next()">Next</button>
<span id="pos" style="color:#94a3b8;font-size:.9rem"></span>
<span style="flex:1"></span>
<button type="button" onclick="toggleFs()">Fullscreen</button>
<span style="color:#5eead4;font-weight:800">Knowsoft Eleon Offline</span>
</header>
<div id="stage"><div class="card" id="card">Loading...</div></div>
<div class="brand">Knowsoft Eleon</div>
<script>
const DECK = __PAYLOAD__;
const SLIDES = DECK.slides || [];
let i = 0, chartInst = null;
function parseChart(s){
  if(!s.chartData) return null;
  try{const j=JSON.parse(s.chartData); if(j.labels&&j.values) return j;}catch(e){}
  const labels=[],values=[];
  String(s.chartData).split(",").forEach(part=>{
    if(part.includes(":")){const idx=part.lastIndexOf(":");const lab=part.slice(0,idx).trim();const val=parseFloat(part.slice(idx+1));
    if(lab&&!isNaN(val)){labels.push(lab);values.push(val);}}
  }); return labels.length?{labels,values}:null;
}
function render(){
  const s=SLIDES[i]||{title:"(empty)",body:"",bg:"#0f172a",accent:"#14b8a6"};
  document.getElementById("pos").textContent=(i+1)+" / "+SLIDES.length+" · "+(DECK.title||"");
  document.getElementById("stage").style.background=s.bg||"#0f172a";
  const imgs=s.images&&s.images.length?s.images:(s.image?[s.image]:[]);
  const photo=imgs[0]||"";
  const parsed=(s.chartType||s.chartEffect==="race"||s.showDataTable)?parseChart(s):null;
  let chartHtml="";
  if(s.chartEffect==="race"&&parsed){
    const max=Math.max(...parsed.values,1);
    chartHtml=parsed.labels.map((lab,idx)=>{const pct=Math.round(parsed.values[idx]/max*100);
      return `<div class="race-row"><div>${lab}</div><div class="race-track"><div class="race-bar" data-pct="${pct}"></div></div><div>${parsed.values[idx]}</div></div>`;}).join("");
  } else if(s.chartType&&parsed){ chartHtml=`<div class="chart-wrap"><canvas id="c"></canvas></div>`; }
  let table="";
  if(s.showDataTable&&parsed){ table=`<table class="dt"><thead><tr><th>Label</th><th>Value</th></tr></thead><tbody>`+parsed.labels.map((l,idx)=>`<tr><td>${l}</td><td style="color:${s.accent};font-weight:800">${parsed.values[idx]}</td></tr>`).join("")+`</tbody></table>`; }
  const multi=imgs.length>1?`<div class="multi">${imgs.slice(0,6).map(u=>`<img src="${u}">`).join("")}</div>`:"";
  const imgOne=photo&&imgs.length<=1?`<img class="photo" src="${photo}">`:"";
  document.getElementById("card").innerHTML=`<h1 style="color:${s.accent||"#5eead4"}">${s.title||""}</h1><div class="body" style="color:${s.fontColor||"#e2e8f0"}">${s.body||""}</div>${chartHtml}${table}${imgOne}${multi}`;
  if(chartInst){try{chartInst.destroy()}catch(e){} chartInst=null;}
  if(s.chartType&&parsed&&s.chartEffect!=="race"&&document.getElementById("c")){
    const accent=s.accent||"#14b8a6"; const palette=[accent,"#6366f1","#f59e0b","#ec4899","#22d3ee"];
    const plugins=(window.ChartDataLabels&&s.chartLabelMode!=="off")?[ChartDataLabels]:[];
    chartInst=new Chart(document.getElementById("c"),{type:s.chartType,data:{labels:parsed.labels,datasets:[{data:parsed.values,backgroundColor:parsed.labels.map((_,i)=>palette[i%palette.length]+"cc"),borderColor:accent,borderWidth:2,tension:.35}]},plugins,options:{responsive:true,maintainAspectRatio:false,animation:{duration:1400},plugins:{legend:{labels:{color:"#e2e8f0"}},datalabels:s.chartLabelMode==="off"?{display:false}:{color:"#fff",font:{weight:"bold"},anchor:s.chartLabelMode==="inside"?"center":"end",align:s.chartLabelMode==="inside"?"center":"top",formatter:v=>v}},scales:(s.chartType==="pie"||s.chartType==="doughnut")?{}:{x:{ticks:{color:"#94a3b8"},grid:{color:"rgba(255,255,255,.06)"}},y:{ticks:{color:"#94a3b8"},grid:{color:"rgba(255,255,255,.06)"},beginAtZero:true}}}});
  }
  document.querySelectorAll(".race-bar").forEach(el=>{requestAnimationFrame(()=>{el.style.width=el.getAttribute("data-pct")+"%";});});
}
function next(){if(i<SLIDES.length-1){i++;render();}}
function prev(){if(i>0){i--;render();}}
function toggleFs(){if(!document.fullscreenElement)document.documentElement.requestFullscreen().catch(()=>{});else document.exitFullscreen();}
document.addEventListener("keydown",e=>{if(e.key==="ArrowRight"||e.key===" "){e.preventDefault();next();}if(e.key==="ArrowLeft"){e.preventDefault();prev();}if(e.key==="f"||e.key==="F")toggleFs();});
render();
</script></body></html>"""

