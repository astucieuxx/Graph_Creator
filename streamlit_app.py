import streamlit as st
import anthropic
import json, re, base64, math

MODEL        = "claude-sonnet-4-5"
SOURCE_COLORS = ["#c8f060", "#60c8f0", "#f060c8", "#f8b450"]

st.set_page_config(page_title="Signal — AI Chart Builder", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
  #MainMenu, footer { display:none; }
  section[data-testid="stSidebar"] { background:#161920; min-width:420px; max-width:420px; }
  .block-container { padding:1.5rem 2rem; background:#0d0f12; min-height:100vh; }
  body { background:#0d0f12; color:#e8eaf0; }
  .stTextInput > div > div > input, .stTextArea > div > div > textarea,
  .stSelectbox > div > div { background:#1e2229 !important; color:#e8eaf0 !important; border-color:#2a2f3a !important; }
  .stButton > button { background:#c8f060; color:#0d0f12; font-weight:700; border:none; }
  .stButton > button:hover { background:#b8e050; color:#0d0f12; }
</style>""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────
for k, v in {"sources": [], "next_id": 1, "chart_title": "", "chart_payload": None}.items():
    if k not in st.session_state:
        st.session_state[k] = v

def get_client():
    return anthropic.Anthropic(api_key=st.secrets["ANTHROPIC_API_KEY"])

def robust_parse(raw):
    cleaned = re.sub(r'^```[\w]*\n?', '', raw, flags=re.MULTILINE).strip()
    cleaned = re.sub(r'```$', '', cleaned, flags=re.MULTILINE).strip()
    try: return json.loads(cleaned)
    except: pass
    m = re.search(r'\{[\s\S]*\}', cleaned)
    if m:
        try: return json.loads(m.group(0))
        except: pass
    return None

def format_val(value, unit):
    if unit in ("%","pct"): return f"{value}%"
    if unit == "B": return f"${value}B"
    if unit == "M": return f"${value}M"
    if unit == "T": return f"${value}T"
    return str(value)

def add_source():
    if len(st.session_state.sources) >= 4:
        st.toast("Maximum 4 sources", icon="⚠️"); return
    i = len(st.session_state.sources)
    st.session_state.sources.append({
        "id": st.session_state.next_id, "color": SOURCE_COLORS[i],
        "name": f"Source {st.session_state.next_id}",
        "publisher": "", "pub_year": "", "article_title": "",
        "mode": "text", "text": "",
        "image_b64": None, "image_mime": None, "points": [],
    })
    st.session_state.next_id += 1

if not st.session_state.sources:
    add_source()

# ── Extraction ────────────────────────────────────────────────────────────────
def extract_data(src):
    system = """Extract ONLY primary market data points. Return valid JSON only:
{"points":[{"year":2025,"value":22.8,"unit":"B","label":"max 5 words"}],"metric_name":"...","currency":"USD"}
Units: B=billions M=millions T=trillions %=percentage raw=no unit.
Only main claims, not contextual refs. Ranges → midpoint. None found → {"points":[],"metric_name":"","currency":"USD"}"""

    if src["mode"] == "text":
        content = [{"type":"text","text":src["text"]}]
    else:
        content = [
            {"type":"image","source":{"type":"base64","media_type":src["image_mime"],"data":src["image_b64"]}},
            {"type":"text","text":"Extract all market data points with years and values from this image."}
        ]
    resp = get_client().messages.create(model=MODEL, max_tokens=1024, system=system,
                                        messages=[{"role":"user","content":content}])
    parsed = robust_parse(resp.content[0].text)
    if not parsed or not parsed.get("points"): return 0
    existing = {p["year"] for p in src["points"]}
    added = 0
    for p in parsed["points"]:
        if p.get("year") and p.get("value") is not None and p["year"] not in existing:
            src["points"].append(p); existing.add(p["year"]); added += 1
    src["points"].sort(key=lambda p: p["year"])
    if parsed.get("metric_name") and not st.session_state.chart_title:
        st.session_state.chart_title = parsed["metric_name"]
    return added

# ── Interpolation ─────────────────────────────────────────────────────────────
def interpolate(points, years, mode):
    if not points: return [None]*len(years)
    m = {p["year"]: p["value"] for p in points}
    ky = sorted(m)
    if mode == "raw":
        return [m.get(y) for y in years]
    if mode == "cagr":
        if len(ky) < 2: return [m[ky[0]]]*len(years)
        n = ky[-1]-ky[0]
        cagr = (m[ky[-1]]/m[ky[0]])**(1/n)-1 if n>0 else 0
        return [round(m[ky[0]]*(1+cagr)**(y-ky[0]),4) for y in years]
    # linear
    result = []
    for y in years:
        if y in m: result.append(m[y]); continue
        if y < ky[0]:
            slope = (m[ky[1]]-m[ky[0]])/(ky[1]-ky[0]) if len(ky)>1 else 0
            result.append(round(m[ky[0]]+slope*(y-ky[0]),4)); continue
        if y > ky[-1]:
            slope = (m[ky[-1]]-m[ky[-2]])/(ky[-1]-ky[-2]) if len(ky)>1 else 0
            result.append(round(m[ky[-1]]+slope*(y-ky[-1]),4)); continue
        lo = max(k for k in ky if k<y); hi = min(k for k in ky if k>y)
        t = (y-lo)/(hi-lo)
        result.append(round(m[lo]+t*(m[hi]-m[lo]),4))
    return result

# ── AI analysis ────────────────────────────────────────────────────────────────
def generate_analysis(active_sources, interp_label, view_mode, metric, unit):
    lines = []
    for s in active_sources:
        pts = ", ".join(str(p["year"]) + ": " + str(p["value"]) + str(p["unit"]) for p in s["points"])
        pub = s["publisher"] or s["name"]
        art = ('"' + s["article_title"] + '"') if s["article_title"] else ""
        lines.append(f"- {pub} {art}: {pts}")
    summary = "\n".join(lines)
    mode_label = "unified single line" if view_mode == "unified" else "one line per source"
    resp = get_client().messages.create(
        model=MODEL, max_tokens=300,
        system="Write exactly 2 sentences (max 45 words): (1) which sources provided which data and how gaps were filled; (2) why combining them is valid. Factual, no filler. Return ONLY JSON: {\"analysis\":\"two sentences\",\"title\":\"concise title\"}",
        messages=[{"role":"user","content":f"Mode:{mode_label}\nInterp:{interp_label}\nMetric:{metric}\nUnit:{unit}\n{summary}"}]
    )
    return robust_parse(resp.content[0].text)

# ── Chart HTML component ───────────────────────────────────────────────────────
CHART_HTML = """<!DOCTYPE html><html><head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/pptxgenjs@3.12.0/dist/pptxgen.bundle.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0f12;color:#e8eaf0;font-family:'Instrument Sans',system-ui,sans-serif;padding:16px}
.header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px;gap:12px}
.title-block{flex:1}
h2{font-family:Georgia,serif;font-size:22px;line-height:1.2;color:#e8eaf0}
.subtitle{font-size:11px;color:#7a8090;font-family:monospace;margin-top:3px}
.actions{display:flex;gap:6px;flex-shrink:0}
.btn{background:none;border:1px solid #2a2f3a;color:#7a8090;font-size:11px;font-weight:600;
  padding:5px 10px;border-radius:6px;cursor:pointer;transition:all .15s;white-space:nowrap}
.btn:hover{border-color:#c8f060;color:#c8f060}
.insight{background:#1e2229;border:1px solid #2a2f3a;border-left:3px solid #60c8f0;
  border-radius:8px;padding:10px 14px;font-size:12px;color:#7a8090;line-height:1.6;margin:12px 0}
.insight strong{color:#e8eaf0;display:block;margin-bottom:2px}
.cits-title{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;
  color:#7a8090;margin:14px 0 6px;display:flex;align-items:center;justify-content:space-between}
.cit{background:#1e2229;border:1px solid #2a2f3a;border-radius:8px;padding:9px 12px;
  margin-bottom:5px;display:flex;gap:10px;align-items:flex-start}
.dot{width:7px;height:7px;border-radius:50%;flex-shrink:0;margin-top:5px}
.cit-apa{font-size:11px;color:#e8eaf0;line-height:1.6}
.cit-apa em{font-style:italic;color:#7a8090}
.cit-pts{font-family:monospace;font-size:10px;color:#7a8090;margin-top:3px}
</style></head><body>
<div class="header">
  <div class="title-block">
    <h2 id="chartTitle"></h2>
    <div class="subtitle" id="chartSubtitle"></div>
  </div>
  <div class="actions">
    <button class="btn" id="themeBtn">☀️ Light</button>
    <button class="btn" id="copyBtn">Copy image</button>
    <button class="btn" id="pngBtn">↓ PNG</button>
    <button class="btn" id="pptxBtn">↓ PPTX</button>
  </div>
</div>
<canvas id="chart"></canvas>
<div class="insight" id="insight" style="display:none">
  <strong>Methodology Note</strong>
  <span id="insightText"></span>
</div>
<div id="citsBlock"></div>
<script>
const D = window.SIGNAL_DATA;
let theme = "dark";
let ci = null;

const tc = {
  dark:  {bg:"#0d0f12",surface:"#1e2229",text:"#e8eaf0",muted:"#7a8090",grid:"#2a2f3a"},
  light: {bg:"#ffffff",surface:"#f5f7fa",text:"#1a1d23",muted:"#6b7280",grid:"#e5e7eb"}
};

document.getElementById("chartTitle").textContent   = D.title;
document.getElementById("chartSubtitle").textContent = D.subtitle;
if (D.analysis) {
  document.getElementById("insightText").textContent = D.analysis;
  document.getElementById("insight").style.display = "block";
}

// Citations
const cb = document.getElementById("citsBlock");
if (D.citations && D.citations.length) {
  const hdr = document.createElement("div");
  hdr.className = "cits-title";
  hdr.innerHTML = `<span>Sources & Citations</span><button class="btn" id="copyCits">Copy citations</button>`;
  cb.appendChild(hdr);
  D.citations.forEach((c,i) => {
    const d = document.createElement("div"); d.className = "cit";
    d.innerHTML = `<div class="dot" style="background:${c.color}"></div>
      <div><div class="cit-apa">[${i+1}] ${c.publisher}. (${c.pub_year}). <em>${c.title}</em>. [${c.pts}]</div>
      <div class="cit-pts">${c.pts}</div></div>`;
    cb.appendChild(d);
  });
  document.getElementById("copyCits").addEventListener("click", () => {
    const txt = D.citations.map((c,i) => `[${i+1}] ${c.publisher}. (${c.pub_year}). ${c.title}. [${c.pts}]`).join("\\n");
    navigator.clipboard.writeText(txt).then(() => alert("Citations copied!"));
  });
}

function buildChart() {
  const t = tc[theme];
  document.body.style.background = t.bg;
  document.body.style.color = t.text;
  document.getElementById("themeBtn").textContent = theme === "dark" ? "☀️ Light" : "🌙 Dark";
  if (ci) { ci.destroy(); ci = null; }
  ci = new Chart(document.getElementById("chart"), {
    type: D.chartType === "scatter" ? "scatter" : D.chartType,
    data: { labels: D.chartType==="scatter" ? undefined : D.years.map(String), datasets: D.datasets },
    options: {
      responsive: true,
      interaction: { mode:"index", intersect:false },
      plugins: {
        legend: { labels:{ color:t.text, font:{family:"monospace",size:11}, boxWidth:12, padding:14 }},
        tooltip: { backgroundColor:t.surface, borderColor:t.grid, borderWidth:1,
          titleColor:t.text, bodyColor:t.muted,
          callbacks: { label: ctx => {
            const v = ctx.parsed.y; if(v==null) return null;
            return ` ${ctx.dataset.label}: ${v.toFixed(2)}${D.unitLabel}`;
          }}
        }
      },
      scales: D.chartType==="scatter" ? {
        x:{ type:"linear", ticks:{color:t.muted,font:{family:"monospace",size:10}}, grid:{color:t.grid}},
        y:{ ticks:{color:t.muted,font:{family:"monospace",size:10},callback:v=>`${v}${D.unitLabel}`}, grid:{color:t.grid}}
      } : {
        x:{ ticks:{color:t.muted,font:{family:"monospace",size:10}}, grid:{color:t.grid}},
        y:{ ticks:{color:t.muted,font:{family:"monospace",size:10},callback:v=>`${v}${D.unitLabel}`}, grid:{color:t.grid}}
      }
    }
  });
}
buildChart();

function exportCanvas() {
  const canvas = document.getElementById("chart");
  const tmp = document.createElement("canvas");
  tmp.width = canvas.width; tmp.height = canvas.height;
  const ctx = tmp.getContext("2d");
  ctx.fillStyle = theme==="light" ? "#ffffff" : "#0d0f12";
  ctx.fillRect(0,0,tmp.width,tmp.height);
  ctx.drawImage(canvas,0,0);
  return tmp;
}

document.getElementById("themeBtn").addEventListener("click", () => {
  theme = theme==="dark" ? "light" : "dark"; buildChart();
});
document.getElementById("copyBtn").addEventListener("click", () => {
  exportCanvas().toBlob(async blob => {
    try { await navigator.clipboard.write([new ClipboardItem({"image/png":blob})]);
      alert("Copied! Paste with ⌘V in Google Slides."); }
    catch(e) { alert("Copy failed — use Download PNG instead."); }
  });
});
document.getElementById("pngBtn").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = exportCanvas().toDataURL("image/png");
  a.download = `${D.title.replace(/\\s+/g,"_")}.png`; a.click();
});
document.getElementById("pptxBtn").addEventListener("click", async () => {
  const pptx = new PptxGenJS();
  pptx.layout = "LAYOUT_WIDE";
  const slide = pptx.addSlide();
  slide.background = { color:"0D0F12" };
  slide.addText(D.title, {x:.4,y:.2,w:12.5,h:.6,fontSize:28,bold:true,color:"E8EAF0",fontFace:"Georgia"});
  slide.addText(D.subtitle, {x:.4,y:.82,w:12.5,h:.28,fontSize:11,color:"7A8090",fontFace:"Courier New"});
  const pType = D.chartType==="bar" ? pptx.ChartType.bar : pptx.ChartType.line;
  const chartData = D.datasets.map(ds => ({
    name: ds.label,
    labels: D.years.map(String),
    values: (ds.data||[]).map(v => v==null ? 0 : parseFloat((+v).toFixed(2)))
  }));
  slide.addChart(pType, chartData, {
    x:.4,y:1.1,w:12.5,h:5.6,
    chartColors: D.datasets.map(ds => (ds.borderColor||"#c8f060").replace("#","")),
    showLegend:true, legendPos:"t", legendFontSize:10, legendColor:"E8EAF0",
    showTitle:false, valAxisLabelColor:"7A8090", catAxisLabelColor:"7A8090",
  });
  if (D.analysis) {
    slide.addText(`Note: ${D.analysis}`,{x:.4,y:6.9,w:12.5,h:.45,fontSize:8,color:"7A8090",fontFace:"Courier New",italic:true});
  }
  await pptx.writeFile({ fileName: `${D.title.replace(/\\s+/g,"_")}.pptx` });
});
</script></body></html>"""

# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    col1, col2 = st.columns([3,1])
    with col1:
        st.markdown("## Signal")
    with col2:
        if st.button("+ Add", use_container_width=True):
            add_source(); st.rerun()

    for idx, src in enumerate(st.session_state.sources):
        color = src["color"]
        st.markdown(f"""<div style="display:flex;align-items:center;gap:8px;margin:8px 0 4px">
          <div style="width:9px;height:9px;border-radius:50%;background:{color};flex-shrink:0"></div>
          <span style="font-weight:600;font-size:13px">{src['name']}</span></div>""",
          unsafe_allow_html=True)

        with st.container():
            # Name + remove
            c1, c2 = st.columns([4,1])
            with c1:
                src["name"] = st.text_input("Name", value=src["name"], key=f"name_{src['id']}", label_visibility="collapsed")
            with c2:
                if st.button("✕", key=f"rm_{src['id']}"):
                    st.session_state.sources.pop(idx); st.rerun()

            # Publisher / year / title
            c1, c2 = st.columns([3,1])
            with c1:
                src["publisher"] = st.text_input("Publisher", value=src["publisher"], placeholder="Publisher (e.g. Gartner)", key=f"pub_{src['id']}", label_visibility="collapsed")
            with c2:
                src["pub_year"] = st.text_input("Year", value=src["pub_year"], placeholder="Year", key=f"yr_{src['id']}", label_visibility="collapsed")
            src["article_title"] = st.text_input("Title", value=src["article_title"], placeholder="Article / Report title", key=f"title_{src['id']}", label_visibility="collapsed")

            # Text / Image toggle
            mode = st.radio("Input mode", ["Text","Image"], key=f"mode_{src['id']}", horizontal=True, label_visibility="collapsed")
            src["mode"] = "text" if mode == "Text" else "image"

            if src["mode"] == "text":
                src["text"] = st.text_area("Paste text", value=src["text"], height=90,
                    placeholder="Paste market research text or report excerpt…",
                    key=f"txt_{src['id']}", label_visibility="collapsed")
            else:
                uploaded = st.file_uploader("Upload image", type=["png","jpg","jpeg","webp"],
                    key=f"img_{src['id']}", label_visibility="collapsed")
                if uploaded:
                    src["image_b64"]  = base64.b64encode(uploaded.read()).decode()
                    src["image_mime"] = uploaded.type
                    st.image(uploaded, use_container_width=True)

            if st.button("Extract Data with AI", key=f"ext_{src['id']}", use_container_width=True):
                with st.spinner("Extracting…"):
                    try:
                        n = extract_data(src)
                        if n > 0: st.success(f"Extracted {n} data point{'s' if n!=1 else ''}"); st.rerun()
                        else: st.warning("No data points found.")
                    except Exception as e:
                        st.error(f"Error: {e}")

            # Data point tags
            if src["points"]:
                for pi, pt in enumerate(src["points"]):
                    c1, c2, c3 = st.columns([1,3,0.5])
                    with c1:
                        new_yr = st.number_input("yr", value=int(pt["year"]), key=f"pt_yr_{src['id']}_{pi}", label_visibility="collapsed", step=1)
                        pt["year"] = int(new_yr)
                    with c2:
                        new_val = st.number_input("val", value=float(pt["value"]), key=f"pt_val_{src['id']}_{pi}", label_visibility="collapsed", format="%.2f")
                        pt["value"] = float(new_val)
                    with c3:
                        if st.button("✕", key=f"rpt_{src['id']}_{pi}"):
                            src["points"].pop(pi); st.rerun()

        st.divider()

    # Chart config
    st.markdown("#### Chart Settings")
    st.session_state.chart_title = st.text_input("Chart title", value=st.session_state.chart_title, placeholder="e.g. Global AI Market Size 2020–2030")
    c1, c2 = st.columns(2)
    with c1: year_from = st.number_input("Year from", value=0, step=1, help="0 = auto")
    with c2: year_to   = st.number_input("Year to",   value=0, step=1, help="0 = auto")
    c1, c2 = st.columns(2)
    with c1: metric_type = st.selectbox("Metric", ["Market Size","Growth Rate","Revenue","Users","Custom"])
    with c2: unit_scale  = st.selectbox("Unit", ["B — Billions","M — Millions","T — Trillions","% — Percent","Raw"])
    interp_mode = st.selectbox("Interpolation", ["Linear","CAGR","Raw only"])
    c1, c2 = st.columns(2)
    with c1: view_mode  = st.radio("View", ["Unified","Compare"], horizontal=True)
    with c2: chart_type = st.radio("Chart", ["Line","Bar","Scatter"], horizontal=True)

    generate = st.button("Generate Unified Chart", use_container_width=True, type="primary")

# ── MAIN AREA ─────────────────────────────────────────────────────────────────
if not generate and not st.session_state.chart_payload:
    st.markdown("""<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;
      height:60vh;color:#7a8090;text-align:center">
      <div style="font-size:48px;opacity:.3">📊</div>
      <h2 style="font-family:Georgia,serif;font-size:24px;color:#e8eaf0;opacity:.3;margin:12px 0 8px">No chart yet</h2>
      <p style="opacity:.5;max-width:280px;line-height:1.6">Add sources on the left, extract data with AI, then generate a unified chart.</p>
    </div>""", unsafe_allow_html=True)

if generate:
    active = [s for s in st.session_state.sources if s["points"]]
    if not active:
        st.error("Extract data from at least one source first.")
    else:
        with st.spinner("Building chart…"):
            # Year range
            all_years = [p["year"] for s in active for p in s["points"]]
            min_y = int(year_from) if year_from else min(all_years)
            max_y = int(year_to)   if year_to   else max(all_years)
            years = list(range(min_y, max_y + 1))

            unit_map  = {"B — Billions":"B","M — Millions":"M","T — Trillions":"T","% — Percent":"%","Raw":"raw"}
            unit      = unit_map[unit_scale]
            unit_label_map = {"B":"$B","M":"$M","T":"$T","%":"%","raw":""}
            unit_label = unit_label_map[unit]
            interp_key = {"Linear":"linear","CAGR":"cagr","Raw only":"raw"}[interp_mode]
            chart_t    = chart_type.lower()
            v_mode     = view_mode.lower()

            # Build datasets
            if v_mode == "unified":
                by_year = {}
                for s in active:
                    for p in s["points"]:
                        by_year.setdefault(p["year"],[]).append({"value":p["value"],"color":s["color"]})
                merged = [{"year":y,"value":sum(v["value"] for v in vs)/len(vs),"unit":unit}
                          for y,vs in sorted(by_year.items())]
                vals = interpolate(merged, years, interp_key)
                known_map = {p["year"]: [v["color"] for v in by_year[p["year"]]] for p in merged}
                pt_colors = [known_map[y][0] if y in known_map else "rgba(200,240,96,0.3)" for y in years]
                pt_radii  = [6 if y in known_map else 3 for y in years]
                datasets  = [{"label":"Unified","data":vals,"borderColor":"rgba(200,240,96,0.8)",
                              "backgroundColor":"rgba(200,240,96,0.08)","pointBackgroundColor":pt_colors,
                              "pointRadius":pt_radii,"tension":.35,"fill":False,"spanGaps":True}]
            else:
                datasets = []
                for s in active:
                    vals = interpolate(s["points"], years, interp_key)
                    datasets.append({"label":s["name"],"data":vals,"borderColor":s["color"],
                                    "backgroundColor":s["color"]+"26","pointBackgroundColor":s["color"],
                                    "pointRadius":4,"tension":.35,"fill":False,"spanGaps":True})

            # AI analysis
            interp_labels = {"linear":"linear interpolation","cagr":"CAGR projection","raw":"raw data only"}
            title    = st.session_state.chart_title or "Market Intelligence Chart"
            analysis = ""
            try:
                result = generate_analysis(active, interp_labels[interp_key], v_mode, metric_type, unit)
                if result:
                    analysis = result.get("analysis","")
                    if not st.session_state.chart_title and result.get("title"):
                        title = result["title"]
            except: pass

            # Citations
            citations = [{"color":s["color"],"publisher":s["publisher"] or s["name"],
                         "pub_year":s["pub_year"] or "n.d.","title":s["article_title"] or "Untitled",
                         "pts":", ".join(f'{p["year"]}: {format_val(p["value"],p["unit"])}' for p in s["points"])}
                        for s in active]

            st.session_state.chart_payload = {
                "title": title, "subtitle": f"{unit_label_map.get(unit,'') or unit}  ·  {min_y}–{max_y}",
                "years": years, "datasets": datasets, "unitLabel": unit_label,
                "chartType": chart_t, "analysis": analysis, "citations": citations,
            }

if st.session_state.chart_payload:
    data_json = json.dumps(st.session_state.chart_payload)
    html = CHART_HTML.replace("<script>", f"<script>\nwindow.SIGNAL_DATA = {data_json};\n", 1)
    st.components.v1.html(html, height=700, scrolling=True)
