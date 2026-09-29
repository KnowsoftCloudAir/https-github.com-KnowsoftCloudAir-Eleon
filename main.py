"""Eleon — Your presentation partner (Knowsoft branding)."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
import secrets
import string
import re
import io
from fastapi.responses import StreamingResponse

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import create_db_and_tables, get_session, engine
from app.live_hub import hub
from app.models import User, UserRole, UserStatus, LoginCode, Presentation, Slide, EvalSession, EvalQuestion, EvalResponse, PresentationQANote, LiveSession, LiveViewer, LiveQuestion, AppSetting, SubscriptionSettings, UserSubscription
from app.auth import (
    hash_password, verify_password, create_token,
    require_user, require_admin, user_from_request,
)

BASE = Path(__file__).resolve().parent

def _find_templates_dir() -> Path:
    """Resolve templates whether app lives at BASE/app or BASE (flat deploy)."""
    candidates = [
        BASE / "app" / "templates",
        BASE / "templates",
        Path.cwd() / "app" / "templates",
        Path.cwd() / "templates",
    ]
    for c in candidates:
        if c.is_dir() and (c / "splash.html").exists():
            return c
    for c in candidates:
        if c.is_dir():
            return c
    # last resort create minimal
    d = BASE / "app" / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d

TEMPLATES_DIR = _find_templates_dir()
STATIC_DIR = BASE / "app" / "static"
if not STATIC_DIR.is_dir():
    STATIC_DIR = BASE / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_SLIDES = STATIC_DIR / "uploads" / "slides"
UPLOAD_SLIDES.mkdir(parents=True, exist_ok=True)
UPLOAD_DOCS = STATIC_DIR / "uploads" / "docs"
UPLOAD_DOCS.mkdir(parents=True, exist_ok=True)


def _restore_packaged_templates() -> None:
    """Copy packaged templates into TEMPLATES_DIR if missing (self-heal incomplete deploys)."""
    import shutil
    packaged = BASE / "app" / "packaged_templates"
    if not packaged.is_dir():
        print("No packaged_templates dir")
        return
    # splash
    src_splash = packaged / "splash.html"
    dst_splash = TEMPLATES_DIR / "splash.html"
    if src_splash.exists() and not dst_splash.exists():
        dst_splash.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_splash, dst_splash)
        print("restored splash.html")
    # presenter/*
    src_pres = packaged / "presenter"
    dst_pres = TEMPLATES_DIR / "presenter"
    if src_pres.is_dir():
        dst_pres.mkdir(parents=True, exist_ok=True)
        for f in src_pres.iterdir():
            if f.is_file():
                target = dst_pres / f.name
                if (not target.exists()) or target.stat().st_size < 100 or f.name in ("present.html", "editor.html", "present_original_live.html", "original_scripts.html"):
                    shutil.copy2(f, target)
                    print("restored presenter/" + f.name)


_restore_packaged_templates()

# Multi-path Jinja loader so either layout works
from jinja2 import ChoiceLoader, FileSystemLoader, Environment
_loader_paths = []
for p in [TEMPLATES_DIR, BASE / "app" / "templates", BASE / "templates", BASE / "app" / "packaged_templates"]:
    if p.is_dir() and str(p) not in _loader_paths:
        _loader_paths.append(str(p))
templates = Jinja2Templates(directory=_loader_paths[0] if _loader_paths else str(TEMPLATES_DIR))
# Replace env loader with choice loader
templates.env.loader = ChoiceLoader([FileSystemLoader(p) for p in _loader_paths])
print(f"Eleon templates dirs: {_loader_paths}")
print(f"  present.html exists: {(TEMPLATES_DIR / 'presenter' / 'present.html').exists()}")
print(f"  packaged present: {(BASE / 'app' / 'packaged_templates' / 'presenter' / 'present.html').exists()}")


def _safe_template(name: str, ctx: dict):
    """Render Jinja template or return a minimal HTML fallback."""
    try:
        return templates.TemplateResponse(name, ctx)
    except Exception as e:
        print(f"template miss {name}:", e)
        from fastapi.responses import HTMLResponse
        p = ctx.get("presentation")
        slides = ctx.get("slides") or []
        pid = getattr(p, "id", 0) if p else 0
        title = getattr(p, "title", "Presentation") if p else "Presentation"
        if "present_original" in name:
            path = ctx.get("pptx_url") or getattr(p, "original_pptx_path", "") or ""
            return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>Original PPT</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-950 text-white p-6 min-h-screen">
<a href="/presentations/{pid}/edit" class="text-teal-300">← Editor</a>
<h1 class="text-2xl font-bold mt-4">{title} — Original PPT</h1>
<p class="text-slate-400 mt-2">Download and open in PowerPoint / LibreOffice (no analysis).</p>
<a class="inline-block mt-6 rounded-xl bg-teal-600 px-5 py-3 font-bold" href="{path}" download>Download PPTX</a>
<a class="inline-block mt-6 ml-2 rounded-xl border border-white/20 px-5 py-3" href="{path}" target="_blank">Open</a>
</body></html>""")
        # present slides fallback
        import json
        slides_js = []
        for s in slides:
            slides_js.append({
                "title": getattr(s, "title", "") or "",
                "body": getattr(s, "body", "") or "",
                "image": getattr(s, "image_path", None) or getattr(s, "online_image_url", None) or "",
                "bg": getattr(s, "bg_color", None) or "#0f172a",
                "accent": getattr(s, "accent", None) or "#14b8a6",
                "chartType": getattr(s, "chart_type", None) or "",
                "chartData": getattr(s, "chart_data", None) or "",
                "chartEffect": getattr(s, "chart_effect", None) or "grow",
                "layout": getattr(s, "layout_style", None) or "title_body",
                "eleonScript": getattr(s, "eleon_script", None) or "",
            })
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-950 text-white min-h-screen flex flex-col">
<header class="p-3 flex flex-wrap gap-2 items-center border-b border-white/10">
<a class="text-teal-300 text-sm" href="/presentations/{pid}/edit">← Editor</a>
<button type="button" onclick="prev()" class="px-3 py-1 rounded bg-slate-800">Prev</button>
<button type="button" onclick="next()" class="px-3 py-1 rounded bg-teal-700 font-bold">Next</button>
<span id="pos" class="text-sm text-slate-400"></span>
</header>
<main id="stage" class="flex-1 flex items-center justify-center p-6"></main>
<script>
const SLIDES = {json.dumps(slides_js)};
let i = 0;
function render() {{
  const s = SLIDES[i] || {{title:"(empty)", body:"", bg:"#0f172a", accent:"#14b8a6"}};
  document.getElementById("pos").textContent = (i+1) + " / " + SLIDES.length;
  const st = document.getElementById("stage");
  st.style.background = s.bg;
  st.innerHTML = "<div class=\"max-w-3xl w-full\"><h1 class=\"text-3xl font-black mb-4\" style=\"color:"+s.accent+"\">"+s.title+"</h1><div class=\"text-lg whitespace-pre-wrap leading-relaxed\">"+s.body+"</div>"+(s.image?"<img src=\""+s.image+"\" class=\"mt-4 max-h-64 rounded-xl\">":"")+"</div>";
}}
function next() {{ if (i < SLIDES.length-1) {{ i++; render(); }} }}
function prev() {{ if (i > 0) {{ i--; render(); }} }}
document.addEventListener("keydown", e => {{ if (e.key==="ArrowRight"||e.key===" ") next(); if (e.key==="ArrowLeft") prev(); }});
render();
</script></body></html>""")




def gen_login_number() -> str:
    return "EL-" + "".join(secrets.choice(string.digits) for _ in range(8))


def gen_code() -> str:
    return "ELEON-" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))


DURATION_DAYS = {"week": 7, "month": 30, "year": 365}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ensure templates on disk even if deploy omitted them
    try:
        _restore_packaged_templates()
        print("restore packaged templates in lifespan")
    except Exception as _re:
        print("restore templates warn", _re)

    create_db_and_tables()

    # Comprehensive column migrations (create_all does not ALTER existing tables)
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            url = str(engine.url).lower()
            is_sqlite = "sqlite" in url

            def table_cols(table):
                try:
                    if is_sqlite:
                        return {r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
                    rows = conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ), {"t": table}).fetchall()
                    return {r[0] for r in rows}
                except Exception as e:
                    print("table_cols", table, e)
                    return set()

            def addcol(table, col, sqlite_ddl, pg_ddl=None):
                c = table_cols(table)
                if not c:
                    return
                if col in c:
                    return
                ddl = sqlite_ddl if is_sqlite else (pg_ddl or sqlite_ddl)
                # postgres often needs different types - simplify
                if not is_sqlite:
                    ddl = ddl.replace("VARCHAR", "VARCHAR")
                    ddl = ddl.replace("BOOLEAN DEFAULT 0", "BOOLEAN DEFAULT FALSE")
                    ddl = ddl.replace("BOOLEAN DEFAULT 1", "BOOLEAN DEFAULT TRUE")
                    ddl = ddl.replace("DATETIME", "TIMESTAMP")
                    ddl = ddl.replace("TEXT DEFAULT ''", "TEXT DEFAULT ''")
                try:
                    conn.execute(text(ddl))
                    print(f"migrated: {table}.{col}")
                except Exception as e:
                    print(f"migrate skip {table}.{col}:", e)

            addcol("user", "sub_status", "ALTER TABLE user ADD COLUMN sub_status VARCHAR DEFAULT 'trial_12h'")
            addcol("user", "sub_ends_at", "ALTER TABLE user ADD COLUMN sub_ends_at DATETIME")
            addcol("user", "free_month_used", "ALTER TABLE user ADD COLUMN free_month_used BOOLEAN DEFAULT 0")
            addcol("user", "sub_plan", "ALTER TABLE user ADD COLUMN sub_plan VARCHAR DEFAULT 'trial'")

            for col, ddl in [
                ("images_json", "ALTER TABLE slide ADD COLUMN images_json TEXT"),
                ("font_family", "ALTER TABLE slide ADD COLUMN font_family VARCHAR DEFAULT 'Inter'"),
                ("font_size", "ALTER TABLE slide ADD COLUMN font_size VARCHAR DEFAULT 'md'"),
                ("font_color", "ALTER TABLE slide ADD COLUMN font_color VARCHAR DEFAULT '#e2e8f0'"),
                ("backdrop_style", "ALTER TABLE slide ADD COLUMN backdrop_style VARCHAR DEFAULT 'none'"),
                ("chart_effect", "ALTER TABLE slide ADD COLUMN chart_effect VARCHAR DEFAULT 'grow'"),
                ("show_data_table", "ALTER TABLE slide ADD COLUMN show_data_table BOOLEAN DEFAULT 0"),
                ("chart_label_mode", "ALTER TABLE slide ADD COLUMN chart_label_mode VARCHAR DEFAULT 'outside'"),
                ("image_path", "ALTER TABLE slide ADD COLUMN image_path VARCHAR"),
                ("online_image_url", "ALTER TABLE slide ADD COLUMN online_image_url VARCHAR"),
                ("layout_style", "ALTER TABLE slide ADD COLUMN layout_style VARCHAR DEFAULT 'title_body'"),
                ("icon_name", "ALTER TABLE slide ADD COLUMN icon_name VARCHAR DEFAULT ''"),
                ("chart_type", "ALTER TABLE slide ADD COLUMN chart_type VARCHAR DEFAULT ''"),
                ("chart_data", "ALTER TABLE slide ADD COLUMN chart_data TEXT"),
                ("pattern", "ALTER TABLE slide ADD COLUMN pattern VARCHAR DEFAULT 'gradient_teal'"),
                ("image_style", "ALTER TABLE slide ADD COLUMN image_style VARCHAR DEFAULT 'frame'"),
                ("word_animation", "ALTER TABLE slide ADD COLUMN word_animation VARCHAR DEFAULT 'fadeUp'"),
                ("word_emphasis", "ALTER TABLE slide ADD COLUMN word_emphasis BOOLEAN DEFAULT 1"),
                ("keyword_animation", "ALTER TABLE slide ADD COLUMN keyword_animation BOOLEAN DEFAULT 1"),
                ("animation_in", "ALTER TABLE slide ADD COLUMN animation_in VARCHAR DEFAULT 'fade'"),
                ("animation_out", "ALTER TABLE slide ADD COLUMN animation_out VARCHAR DEFAULT 'fade'"),
                ("bg_color", "ALTER TABLE slide ADD COLUMN bg_color VARCHAR DEFAULT '#0f172a'"),
                ("accent", "ALTER TABLE slide ADD COLUMN accent VARCHAR DEFAULT '#14b8a6'"),
                ("notes", "ALTER TABLE slide ADD COLUMN notes TEXT"),
                ("extra_data", "ALTER TABLE slide ADD COLUMN extra_data TEXT"),
            ]:
                addcol("slide", col, ddl)

            addcol("presentation", "original_pptx_path", "ALTER TABLE presentation ADD COLUMN original_pptx_path VARCHAR")
            addcol("presentation", "original_pptx_scripts", "ALTER TABLE presentation ADD COLUMN original_pptx_scripts TEXT")
            addcol("presentation", "logo_path", "ALTER TABLE presentation ADD COLUMN logo_path VARCHAR")
            addcol("presentation", "footer_text", "ALTER TABLE presentation ADD COLUMN footer_text VARCHAR DEFAULT ''")
            addcol("presentation", "default_pattern", "ALTER TABLE presentation ADD COLUMN default_pattern VARCHAR DEFAULT 'gradient_teal'")
            addcol("presentation", "share_token", "ALTER TABLE presentation ADD COLUMN share_token VARCHAR")
    except Exception as e:
        print("migrate warn:", e)
        import traceback
        traceback.print_exc()

    from sqlmodel import Session as S
    with S(engine) as session:
        # --- Admin (always reset password so login works) ---
        admin = session.exec(select(User).where(User.email == "admin@eleon.knowsoft")).first()
        if not admin:
            admin = User(
                email="admin@eleon.knowsoft",
                full_name="Eleon General Admin",
                hashed_password=hash_password("admin123"),
                role=UserRole.general_admin,
                status=UserStatus.approved,
                login_number="EL-ADMIN",
            )
            session.add(admin)
        else:
            admin.hashed_password = hash_password("admin123")
            admin.status = UserStatus.approved
            admin.role = UserRole.general_admin
            session.add(admin)

        # --- Demo presenter ---
        demo = session.exec(select(User).where(User.email == "demo@eleon.app")).first()
        if not demo:
            demo = User(
                email="demo@eleon.app",
                full_name="Demo Presenter",
                hashed_password=hash_password("demo123"),
                role=UserRole.presenter,
                status=UserStatus.approved,
                login_number="EL-DEMO001",
                access_expires_at=datetime.utcnow() + timedelta(days=365),
                sub_status="active",
                sub_ends_at=datetime.utcnow() + timedelta(days=365),
                free_month_used=True,
                sub_plan="annual",
            )
            session.add(demo)
            session.commit()
            session.refresh(demo)
        else:
            demo.hashed_password = hash_password("demo123")
            demo.status = UserStatus.approved
            demo.access_expires_at = datetime.utcnow() + timedelta(days=365)
            session.add(demo)
            session.commit()
            session.refresh(demo)


        # Seed default subscription settings
        try:
            from app.models import SubscriptionSettings as _SS
            if not session.exec(select(_SS)).first():
                session.add(_SS(
                    title="Eleon subscription",
                    currency="NGN",
                    monthly_price=3000.0,
                    annual_price=30000.0,
                    bank_name="GTBank",
                    account_name="Knowsoft Technologies",
                    account_number="0123456789",
                    instructions="Transfer to the account below. Use your email as narration, then upload evidence. Admin will activate your plan.",
                ))
                session.commit()
                print("Seeded SubscriptionSettings")
        except Exception as se:
            print("sub settings seed:", se)

        # --- Demo presentation (always ensure rich 5-slide showcase) ---
        pres = session.exec(
            select(Presentation).where(Presentation.owner_id == demo.id, Presentation.title == "Eleon Demo Deck")
        ).first()
        if pres:
            nslides = len(session.exec(select(Slide).where(Slide.presentation_id == pres.id)).all())
            if nslides < 5:
                for s in session.exec(select(Slide).where(Slide.presentation_id == pres.id)).all():
                    session.delete(s)
                session.commit()
                session.delete(pres)
                session.commit()
                pres = None
        if not pres:
            pres = Presentation(owner_id=demo.id, title="Eleon Demo Deck", theme="churchgate")
            session.add(pres)
            session.commit()
            session.refresh(pres)
            # Showcase deck: 5 slides — pictures + all chart types
            demo_slides = [
                {
                    "title": "Welcome to Knowsoft Eleon",
                    "body": "Your presentation partner.\n• Build slides with live preview\n• Charts, images & voice probe\n• Present online or offline pack",
                    "extra_data": "Knowsoft Eleon demo",
                    "icon_name": "rocket",
                    "pattern": "gradient_teal",
                    "animation_in": "float3d",
                    "online_image_url": "https://images.unsplash.com/photo-1557804506-669a67965ba0?w=900&h=500&fit=crop",
                    "layout_style": "image_right",
                    "chart_type": "",
                    "chart_data": "",
                    "chart_effect": "grow",
                    "eleon_script": "Welcome to Knowsoft Eleon, your presentation partner. Build slides, add charts and images, and present with the Eleon voice probe.",
                },
                {
                    "title": "Bar chart — attendance",
                    "body": "Sample numeric series as a professional bar chart.\nEleon grows the bars during presentation.",
                    "extra_data": "Knowsoft Eleon demo",
                    "icon_name": "chart",
                    "pattern": "mesh_indigo",
                    "animation_in": "zoom",
                    "online_image_url": "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=900&h=500&fit=crop",
                    "layout_style": "title_body",
                    "chart_type": "bar",
                    "chart_data": "Mon:42,Tue:55,Wed:48,Thu:61,Fri:70",
                    "chart_effect": "grow",
                    "show_data_table": True,
                    "chart_label_mode": "outside",
                    "eleon_script": "Here is a bar chart of weekly attendance. Values grow on entry so the audience sees the trend clearly.",
                },
                {
                    "title": "Pie & line — mix of views",
                    "body": "Pie for share of categories.\nUse line charts for trends over time.",
                    "extra_data": "Knowsoft Eleon demo",
                    "icon_name": "growth",
                    "pattern": "aurora",
                    "animation_in": "slideLeft",
                    "online_image_url": "https://images.unsplash.com/photo-1551288049-bebda4e38f71?w=900&h=500&fit=crop",
                    "layout_style": "image_left",
                    "chart_type": "pie",
                    "chart_data": "Youth:35,Adults:40,Seniors:15,Visitors:10",
                    "chart_effect": "3d",
                    "chart_label_mode": "inside",
                    "eleon_script": "This pie chart shows category share. Three D perspective and bold labels make the story easy to follow.",
                },
                {
                    "title": "Race numbers — impact",
                    "body": "Racing bars animate values competing side by side.\nGreat for fundraising or KPI showdowns.",
                    "extra_data": "Knowsoft Eleon demo",
                    "icon_name": "target",
                    "pattern": "sunset",
                    "animation_in": "flip",
                    "online_image_url": "https://images.unsplash.com/photo-1504384764586-bb4cdc1707b0?w=900&h=500&fit=crop",
                    "layout_style": "title_body",
                    "chart_type": "bar",
                    "chart_data": "Goal A:80,Goal B:65,Goal C:92,Goal D:50",
                    "chart_effect": "race",
                    "show_data_table": True,
                    "eleon_script": "Race mode. Watch each goal bar run across the track. Use this for impact metrics and friendly competition.",
                },
                {
                    "title": "Thank you — try it yourself",
                    "body": "• Edit any slide in the preparation panel\n• Add your own charts and pictures\n• Present with Eleon probe & autoplay\n• Export Offline pack or PowerPoint",
                    "extra_data": "Knowsoft Eleon",
                    "icon_name": "star",
                    "pattern": "gold_lines",
                    "animation_in": "zoom",
                    "online_image_url": "https://images.unsplash.com/photo-1522071820081-009f0129c71c?w=900&h=500&fit=crop",
                    "layout_style": "centered",
                    "chart_type": "doughnut",
                    "chart_data": "Done:70,Next:20,Ideas:10",
                    "chart_effect": "grow",
                    "chart_label_mode": "outside",
                    "eleon_script": "Thank you for watching this short tour. Open the editor, create your own slides, and present with Eleon.",
                },
            ]
            for i, pl in enumerate(demo_slides):
                sk = dict(
                    presentation_id=pres.id,
                    position=i,
                    title=pl["title"],
                    body=pl["body"],
                    extra_data=pl.get("extra_data") or "",
                    icon_name=pl.get("icon_name") or "",
                    pattern=pl.get("pattern") or "gradient_teal",
                    animation_in=pl.get("animation_in") or "fade",
                    animation_out="fade",
                    online_image_url=pl.get("online_image_url"),
                    layout_style=pl.get("layout_style") or "title_body",
                    chart_type=pl.get("chart_type") or "",
                    chart_data=pl.get("chart_data") or "",
                    bg_color="#0f172a",
                    accent="#14b8a6",
                    word_emphasis=True,
                    keyword_animation=True,
                )
                if hasattr(Slide, "chart_effect"):
                    sk["chart_effect"] = pl.get("chart_effect") or "grow"
                if hasattr(Slide, "show_data_table"):
                    sk["show_data_table"] = bool(pl.get("show_data_table"))
                if hasattr(Slide, "chart_label_mode"):
                    sk["chart_label_mode"] = pl.get("chart_label_mode") or "outside"
                if hasattr(Slide, "eleon_script"):
                    sk["eleon_script"] = pl.get("eleon_script") or ""
                if hasattr(Slide, "images_json") and pl.get("online_image_url"):
                    import json as _json
                    sk["images_json"] = _json.dumps([pl["online_image_url"]])
                session.add(Slide(**sk))
            session.commit()
        session.commit()
        print("✅ Admin: admin@eleon.knowsoft / admin123")
        print("✅ Demo:  demo@eleon.app / demo123  (login number EL-DEMO001 optional)")
    yield


app = FastAPI(title="Eleon", lifespan=lifespan)

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code in (401, 303) and "text/html" in (request.headers.get("accept") or ""):
        if exc.status_code == 401 or (exc.headers and exc.headers.get("Location") == "/login"):
            return RedirectResponse("/login", status_code=303)
    from fastapi.responses import JSONResponse as _JC
    return _JC({"detail": exc.detail}, status_code=exc.status_code)

_static = BASE / "app" / "static"
if not _static.is_dir():
    _static = BASE / "static"
_static.mkdir(parents=True, exist_ok=True)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Avoid opaque 500s during debugging; still log full traceback."""
    import traceback
    traceback.print_exc()
    accept = request.headers.get("accept") or ""
    if "text/html" in accept:
        detail = str(exc).replace("<", "&lt;")[:800]
        html = f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Error</title>
        <script src="https://cdn.tailwindcss.com"></script></head>
        <body class="min-h-screen bg-slate-950 text-slate-100 p-8">
        <h1 class="text-2xl font-bold text-rose-300">Something went wrong</h1>
        <p class="mt-2 text-slate-400 text-sm">Eleon hit an error on this page. Details for the admin log:</p>
        <pre class="mt-4 text-xs bg-black/40 p-4 rounded-xl overflow-auto text-amber-100">{detail}</pre>
        <p class="mt-4"><a class="text-teal-300" href="/">Home</a> · <a class="text-teal-300" href="/dashboard">Dashboard</a></p>
        </body></html>"""
        return HTMLResponse(html, status_code=500)
    return JSONResponse({"detail": str(exc)}, status_code=500)


app.mount("/static", StaticFiles(directory=str(_static)), name="static")



def extract_text_from_upload(filename: str, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith(".txt") or name.endswith(".md"):
        return data.decode("utf-8", errors="ignore")
    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
            return "\n".join(parts)
        except Exception as e:
            return f"[PDF extract error: {e}]"
    return data.decode("utf-8", errors="ignore")



def document_to_slide_payloads(text: str, max_slides: int = 10) -> list:
    """Extract topics, subtopics and key points into up to max_slides content slides + Thank you.
    Every slide carries Knowsoft Eleon branding via extra_data footer note.
    """
    text = (text or "").strip()
    brand = "Knowsoft Eleon"
    if not text:
        return [
            {"title": "Empty document", "body": "No text extracted.", "extra_data": brand, "pattern": "gradient_teal", "icon_name": "book"},
            {"title": "Thank you", "body": "Thank you for your attention.\\nAny questions?", "extra_data": brand, "pattern": "aurora", "icon_name": "spark"},
        ]

    # Split by headings / numbered sections / ALL-CAPS titles / double newlines
    blocks = re.split(
        r"\n(?=#{1,3}\s+|\d+[\.\)]\s+[A-Z]|[A-Z][A-Z0-9 ,\-]{10,}$)",
        text,
    )
    if len(blocks) < 2:
        paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if len(p.strip()) > 40]
        blocks = paras if paras else [text]

    slides = []
    for i, block in enumerate(blocks):
        if len(slides) >= max_slides:
            break
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        title = re.sub(r"^#{1,3}\s*", "", lines[0])
        title = re.sub(r"^\d+[\.\)]\s*", "", title)[:100]
        # Bullet / key points from remaining lines
        points = []
        for ln in lines[1:]:
            ln2 = re.sub(r"^[\-\*•●▪]\s*", "", ln)
            ln2 = re.sub(r"^\d+[\.\)]\s*", "", ln2)
            if len(ln2) > 8:
                points.append("• " + ln2[:180])
            if len(points) >= 6:
                break
        if not points:
            body_src = " ".join(lines[1:]) if len(lines) > 1 else lines[0]
            # sentence chunks as points
            sents = re.split(r"(?<=[.!?])\s+", body_src)
            for s in sents:
                s = s.strip()
                if len(s) > 20:
                    points.append("• " + s[:180])
                if len(points) >= 5:
                    break
        body = "\n".join(points) if points else (lines[0][:400])
        patterns = ["gradient_teal", "mesh_indigo", "aurora", "orbs", "waves", "grid_dark", "sunset", "gold_lines"]
        icons = ["book", "idea", "target", "growth", "chart", "star", "globe", "check", "team", "spark"]
        slides.append({
            "title": title or f"Topic {i+1}",
            "body": body,
            "extra_data": f"{brand} · Document insight {i+1}\n" + block.strip()[:1800],
            "pattern": patterns[i % len(patterns)],
            "icon_name": icons[i % len(icons)],
            "animation_in": ["float3d", "cube", "zoom", "slideUp", "bounceIn"][i % 5],
            "layout_style": "title_body",
        })

    if not slides:
        slides.append({
            "title": "Document overview",
            "body": text[:600],
            "extra_data": brand + "\n" + text[:3000],
            "pattern": "gradient_teal",
            "icon_name": "book",
        })

    # Cap content slides at max_slides then thank-you
    slides = slides[:max_slides]
    slides.append({
        "title": "Thank you",
        "body": "Thank you for your attention.\\nAny questions?\\n\\nPowered by Knowsoft Eleon",
        "extra_data": brand + "\n" + text[:2000],
        "pattern": "aurora",
        "icon_name": "spark",
        "animation_in": "zoom",
        "layout_style": "centered",
    })
    return slides


def pptx_to_slide_payloads(data: bytes, max_slides: int = 40) -> list:
    """Import PPTX keeping each slide structure; seed eleon_script from notes/body."""
    try:
        from pptx import Presentation as PP
    except Exception as e:
        return [{"title": "PowerPoint import", "body": f"python-pptx required: {e}", "extra_data": "Knowsoft Eleon", "eleon_script": ""}]
    import io
    prs = PP(io.BytesIO(data))
    brand = "Knowsoft Eleon · Original PPT structure"
    payloads = []
    for si, slide in enumerate(prs.slides):
        if len(payloads) >= max_slides:
            break
        titles, bodies, notes_txt = [], [], ""
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            text = (shape.text_frame.text or "").strip()
            if not text:
                continue
            if not titles and len(text) < 120 and "\n" not in text:
                titles.append(text)
            else:
                bodies.append(text)
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_txt = (slide.notes_slide.notes_text_frame.text or "").strip()
        except Exception:
            notes_txt = ""
        title = titles[0] if titles else f"Slide {si + 1}"
        body = "\n".join(bodies) if bodies else ""
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        if len(lines) > 1:
            body = "\n".join(("• " + ln if not ln.startswith(("•", "-", "*")) else ln) for ln in lines[:14])
        script = notes_txt or body[:900]
        payloads.append({
            "title": title[:200],
            "body": body[:4000],
            "extra_data": brand,
            "notes": notes_txt[:2000],
            "eleon_script": script[:2000],
            "pattern": "gradient_teal",
            "layout_style": "title_body",
            "animation_in": "float3d",
            "icon_name": "chart" if si % 3 == 1 else ("idea" if si % 3 == 2 else "target"),
        })
    if not payloads:
        payloads.append({"title": "Empty PPT", "body": "No text found.", "extra_data": brand, "eleon_script": ""})
    payloads.append({
        "title": "Thank you",
        "body": "Thank you for your attention.\nQuestions are welcome.",
        "extra_data": brand,
        "eleon_script": "Thank you for your attention. I am happy to take questions.",
        "pattern": "aurora",
        "icon_name": "star",
    })
    return payloads



@app.get("/manifest.webmanifest")
@app.get("/manifest.json")
async def web_manifest():
    path = BASE / "app" / "static" / "manifest.webmanifest"
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/sw.js")
async def service_worker():
    path = BASE / "app" / "static" / "sw.js"
    return FileResponse(path, media_type="application/javascript")


@app.get("/", response_class=HTMLResponse)
async def splash(request: Request, session: Session = Depends(get_session)):
    user = user_from_request(request, session)
    try:
        return templates.TemplateResponse("splash.html", {"request": request, "user": user})
    except Exception as e:
        print("splash template error:", e)
        # emergency fallback so the service never 500s on home
        name = (user.full_name if user else "guest")
        html = f"""<!DOCTYPE html><html><head><meta charset=utf-8><title>Eleon</title>
        <script src="https://cdn.tailwindcss.com"></script></head>
        <body class="min-h-screen bg-slate-950 text-white flex items-center justify-center p-6">
        <div class="max-w-md text-center space-y-4">
          <h1 class="text-3xl font-black text-teal-300">Knowsoft Eleon</h1>
          <p class="text-slate-400">Your presentation partner</p>
          <p class="text-xs text-rose-300">Template note: {e}</p>
          <div class="flex gap-3 justify-center">
            <a class="rounded-xl bg-teal-600 px-4 py-2 font-bold" href="/login">Sign in</a>
            <a class="rounded-xl border border-white/20 px-4 py-2" href="/register">Register</a>
            <a class="rounded-xl border border-white/20 px-4 py-2" href="/dashboard">Dashboard</a>
          </div>
        </div></body></html>"""
        return HTMLResponse(html)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})


@app.post("/login")
async def login_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    email_n = (email or "").strip().lower()
    user = session.exec(select(User).where(User.email == email_n)).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse("auth/login.html", {
            "request": request, "error": "Invalid email or password. Try demo@eleon.app / demo123",
        }, status_code=400)
    if user.role != UserRole.general_admin:
        if user.status == UserStatus.suspended:
            return templates.TemplateResponse("auth/login.html", {
                "request": request, "error": "Account suspended",
            }, status_code=403)
        if user.status != UserStatus.approved:
            return templates.TemplateResponse("auth/login.html", {
                "request": request, "error": "Account pending approval",
            }, status_code=403)
        if user.access_expires_at and user.access_expires_at < datetime.utcnow():
            return templates.TemplateResponse("auth/login.html", {
                "request": request, "error": "Access expired — ask admin for a new code",
            }, status_code=403)
    token = create_token(user.id)
    dest = "/admin" if user.role == UserRole.general_admin else "/dashboard"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("eleon_token", token, httponly=True, max_age=14 * 86400, samesite="lax", path="/")
    return resp


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})


@app.post("/register")
async def register_post(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    access_code: str = Form(""),
    session: Session = Depends(get_session),
):
    email = email.strip().lower()
    if session.exec(select(User).where(User.email == email)).first():
        return templates.TemplateResponse("auth/register.html", {
            "request": request, "error": "Email already registered",
        }, status_code=400)
    now = datetime.utcnow()
    user = User(
        email=email,
        full_name=full_name.strip(),
        hashed_password=hash_password(password),
        role=UserRole.presenter,
        status=UserStatus.pending,
        sub_status="trial_12h",
        sub_ends_at=now + timedelta(hours=12),
        free_month_used=False,
        sub_plan="trial_12h",
    )
    # Optional: redeem access code at registration
    code_row = None
    if access_code.strip():
        code_row = session.exec(
            select(LoginCode).where(LoginCode.code == access_code.strip().upper(), LoginCode.is_used == False)
        ).first()
        if code_row:
            days = DURATION_DAYS.get(code_row.duration, 30)
            user.status = UserStatus.approved
            user.login_number = gen_login_number()
            user.access_expires_at = datetime.utcnow() + timedelta(days=days)
            code_row.is_used = True
            code_row.issued_to_email = email
            session.add(code_row)
    session.add(user)
    session.commit()
    session.refresh(user)
    if code_row:
        msg = f"Approved! Your login number is {user.login_number}. Access for {code_row.duration}."
    else:
        msg = "Registration received. Wait for admin approval and your login number."
    return templates.TemplateResponse("auth/register.html", {
        "request": request, "success": msg,
    })


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie("eleon_token")
    return resp


# ---------- Presenter dashboard ----------
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    pres = session.exec(
        select(Presentation).where(Presentation.owner_id == user.id).order_by(Presentation.updated_at.desc())
    ).all()
    return templates.TemplateResponse("presenter/dashboard.html", {
        "request": request, "user": user, "presentations": pres,
    })


@app.post("/presentations/new")
async def new_presentation(
    title: str = Form("Untitled presentation"),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = Presentation(owner_id=user.id, title=title.strip() or "Untitled presentation")
    session.add(p)
    session.commit()
    session.refresh(p)
    # starter slide
    session.add(Slide(
        presentation_id=p.id, position=0, title="Welcome",
        body="Start editing this slide.\nAdd images and notes for Eleon.",
        extra_data="This is a new Eleon presentation.",
    ))
    session.commit()
    return RedirectResponse(f"/presentations/{p.id}/edit", status_code=303)


@app.get("/presentations/{pid}/edit", response_class=HTMLResponse)
async def edit_presentation(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(
        select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)
    ).all()
    # Serializable dicts for Jinja tojson (ORM objects are not JSON-serializable)
    slides_json = []
    for s in slides:
        slides_json.append({
            "id": s.id,
            "title": s.title or "",
            "body": s.body or "",
            "extra_data": s.extra_data or "",
            "notes": s.notes or "",
            "image_path": s.image_path,
            "images_json": getattr(s, "images_json", None),
            "animation_in": s.animation_in or "fade",
            "animation_out": s.animation_out or "fade",
            "bg_color": s.bg_color or "#0f172a",
            "accent": s.accent or "#14b8a6",
            "layout_style": s.layout_style or "title_body",
            "icon_name": s.icon_name or "",
            "chart_type": s.chart_type or "",
            "chart_data": s.chart_data or "",
            "word_animation": s.word_animation or "fadeUp",
            "word_emphasis": bool(getattr(s, "word_emphasis", True)),
            "online_image_url": s.online_image_url,
            "pattern": s.pattern or "gradient_teal",
            "image_style": s.image_style or "frame",
            "position": s.position,
        })
    return templates.TemplateResponse("presenter/editor.html", {
        "request": request, "user": user, "presentation": p, "slides": slides,
        "slides_json": slides_json,
    })


@app.post("/presentations/{pid}/slides")
async def add_slide(
    pid: int,
    title: str = Form("New slide"),
    body: str = Form(""),
    extra_data: str = Form(""),
    notes: str = Form(""),
    animation_in: str = Form("fade"),
    animation_out: str = Form("fade"),
    bg_color: str = Form("#0f172a"),
    accent: str = Form("#14b8a6"),
    layout_style: str = Form("title_body"),
    icon_name: str = Form(""),
    chart_type: str = Form(""),
    chart_data: str = Form(""),
    keyword_animation: str = Form("on"),
    word_animation: str = Form("fadeUp"),
    online_image_url: str = Form(""),
    pattern: str = Form("gradient_teal"),
    image_style: str = Form("frame"),
    word_emphasis: str = Form("on"),
    image_path: str = Form(""),
    images_json: str = Form(""),
    font_family: str = Form("Inter"),
    font_size: str = Form("md"),
    font_color: str = Form("#e2e8f0"),
    backdrop_style: str = Form("none"),
    chart_effect: str = Form("grow"),
    show_data_table: str = Form("off"),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    n = len(session.exec(select(Slide).where(Slide.presentation_id == pid)).all())
    slide_kwargs = dict(
        presentation_id=pid, position=n, title=title.strip(),
        body=body, extra_data=extra_data or notes,
        notes=notes,
        animation_in=animation_in, animation_out=animation_out,
        bg_color=bg_color, accent=accent,
        layout_style=layout_style or "title_body",
        icon_name=icon_name or "",
        chart_type=chart_type or "",
        chart_data=chart_data or "",
        keyword_animation=(keyword_animation=="on"),
        word_animation=word_animation or "fadeUp",
        online_image_url=(online_image_url or "").strip() or None,
        pattern=pattern or "gradient_teal",
        image_style=image_style or "frame",
        word_emphasis=(word_emphasis=="on"),
        image_path=image_path or None,
    )
    # images_json may be missing on older DBs until migration runs
    try:
        from sqlalchemy import inspect as sa_inspect
        cols = {c["name"] for c in sa_inspect(engine).get_columns("slide")}
        if "images_json" in cols:
            slide_kwargs["images_json"] = images_json or None
        for col, val in [
            ("font_family", font_family or "Inter"),
            ("font_size", font_size or "md"),
            ("font_color", font_color or "#e2e8f0"),
            ("backdrop_style", backdrop_style or "none"),
            ("chart_effect", chart_effect or "grow"),
            ("show_data_table", show_data_table == "on"),
        ]:
            if col in cols:
                slide_kwargs[col] = val
    except Exception:
        pass
    session.add(Slide(**slide_kwargs))
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit", status_code=303)


@app.post("/presentations/{pid}/slides/{sid}")
async def update_slide(
    pid: int, sid: int,
    title: str = Form(""),
    body: str = Form(""),
    extra_data: str = Form(""),
    animation_in: str = Form("fade"),
    animation_out: str = Form("fade"),
    bg_color: str = Form("#0f172a"),
    accent: str = Form("#14b8a6"),
    notes: str = Form(""),
    layout_style: str = Form("title_body"),
    icon_name: str = Form(""),
    chart_type: str = Form(""),
    chart_data: str = Form(""),
    keyword_animation: str = Form("on"),
    word_animation: str = Form("fadeUp"),
    online_image_url: str = Form(""),
    pattern: str = Form("gradient_teal"),
    image_style: str = Form("frame"),
    word_emphasis: str = Form("on"),
    image_path: str = Form(""),
    images_json: str = Form(""),
    font_family: str = Form("Inter"),
    font_size: str = Form("md"),
    font_color: str = Form("#e2e8f0"),
    backdrop_style: str = Form("none"),
    chart_effect: str = Form("grow"),
    show_data_table: str = Form("off"),
    chart_label_mode: str = Form("outside"),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    s = session.get(Slide, sid)
    if not p or not s or p.owner_id != user.id or s.presentation_id != pid:
        raise HTTPException(404)
    s.title = title
    s.body = body
    s.extra_data = extra_data
    s.animation_in = animation_in
    s.animation_out = animation_out
    s.bg_color = bg_color
    s.accent = accent
    s.notes = notes
    s.layout_style = layout_style or "title_body"
    s.icon_name = icon_name or ""
    s.chart_type = chart_type or ""
    s.chart_data = chart_data or ""
    s.keyword_animation = (keyword_animation == "on")
    s.word_animation = word_animation or "fadeUp"
    s.online_image_url = (online_image_url or "").strip() or None
    s.pattern = pattern or "gradient_teal"
    s.image_style = image_style or "frame"
    s.word_emphasis = (word_emphasis == "on")
    if image_path:
        s.image_path = image_path
    if images_json and hasattr(s, "images_json"):
        s.images_json = images_json
    for attr, val in [
        ("font_family", font_family or "Inter"),
        ("font_size", font_size or "md"),
        ("font_color", font_color or "#e2e8f0"),
        ("backdrop_style", backdrop_style or "none"),
        ("chart_effect", chart_effect or "grow"),
        ("chart_label_mode", chart_label_mode or "outside"),
    ]:
        if hasattr(s, attr):
            setattr(s, attr, val)
    if hasattr(s, "show_data_table"):
        s.show_data_table = (show_data_table == "on")
    p.updated_at = datetime.utcnow()
    session.add(s)
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit?sid={sid}", status_code=303)


@app.get("/presentations/{pid}/notes.txt")
async def generate_notes(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)).all()
    lines = [f"PRESENTATION NOTES — {p.title}", f"Generated by Eleon (Knowsoft)", ""]
    for i, s in enumerate(slides, 1):
        lines.append(f"=== SLIDE {i}: {s.title} ===")
        lines.append(s.body or "")
        if s.extra_data:
            lines.append("--- Eleon data ---")
            lines.append(s.extra_data)
        if s.notes:
            lines.append("--- Presenter notes ---")
            lines.append(s.notes)
        lines.append("")
    text = "\n".join(lines)
    return StreamingResponse(io.BytesIO(text.encode("utf-8")), media_type="text/plain",
                             headers={"Content-Disposition": f'attachment; filename="eleon_notes_{pid}.txt"'})


@app.post("/presentations/{pid}/slides/{sid}/image")
async def upload_slide_image(
    pid: int, sid: int,
    image: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    s = session.get(Slide, sid)
    if not p or not s or p.owner_id != user.id or s.presentation_id != pid:
        raise HTTPException(404)
    data = await image.read()
    if len(data) > 8_000_000:
        raise HTTPException(400, "Image too large")
    ext = Path(image.filename or "img.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    fname = f"s{sid}_{secrets.token_hex(6)}{ext}"
    (UPLOAD_SLIDES / fname).write_bytes(data)
    url = f"/static/uploads/slides/{fname}"
    s.image_path = url
    # keep multi-image list
    try:
        import json as _json
        arr = []
        if getattr(s, "images_json", None):
            try:
                arr = _json.loads(s.images_json) if isinstance(s.images_json, str) else list(s.images_json or [])
            except Exception:
                arr = []
        if url not in arr:
            arr.insert(0, url)
        if hasattr(s, "images_json"):
            s.images_json = _json.dumps(arr)
    except Exception as e:
        print("images_json update", e)
    session.add(s)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit?sid={sid}", status_code=303)


@app.post("/presentations/{pid}/slides/{sid}/delete")
async def delete_slide(
    pid: int, sid: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    s = session.get(Slide, sid)
    if not p or not s or p.owner_id != user.id:
        raise HTTPException(404)
    session.delete(s)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit", status_code=303)


@app.post("/presentations/{pid}/settings")
async def presentation_settings(
    pid: int,
    footer_text: str = Form(""),
    default_pattern: str = Form("gradient_teal"),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    logo: UploadFile = File(None),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    p.footer_text = (footer_text or "")[:500]
    p.default_pattern = default_pattern or "gradient_teal"
    if logo and getattr(logo, "filename", None):
        data = await logo.read()
        if data and len(data) < 5_000_000:
            ext = (logo.filename.rsplit(".", 1)[-1] or "png").lower()[:4]
            name = f"logo_{pid}_{secrets.token_hex(4)}.{ext}"
            dest = UPLOAD_SLIDES / name
            dest.write_bytes(data)
            p.logo_path = f"/static/uploads/slides/{name}"
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit", status_code=303)



@app.post("/presentations/{pid}/parse-chart-data")
async def parse_chart_data(
    pid: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    datafile: UploadFile = File(...),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    raw = await datafile.read()
    fname = (datafile.filename or "").lower()
    labels, values, rows = [], [], []
    if fname.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                if not row or row[0] is None:
                    continue
                lab = str(row[0]).strip()
                try:
                    val = float(row[1]) if len(row) > 1 and row[1] is not None else None
                except (TypeError, ValueError):
                    continue
                if lab and val is not None:
                    labels.append(lab[:40])
                    values.append(val)
                    rows.append({"label": lab[:40], "value": val})
        except Exception as e:
            raise HTTPException(400, f"Excel read error: {e}")
    elif fname.endswith((".csv", ".txt")):
        text = raw.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            parts = [x.strip() for x in line.replace(";", ",").split(",")]
            if len(parts) >= 2:
                try:
                    v = float(parts[1])
                    labels.append(parts[0][:40])
                    values.append(v)
                    rows.append({"label": parts[0][:40], "value": v})
                except ValueError:
                    continue
    elif fname.endswith(".pdf"):
        text = extract_text_from_upload(fname, raw)
        for m in re.finditer(r"([A-Za-z][A-Za-z0-9 /%\-]{1,30})\s*[:\|\-]?\s*(\d+(?:\.\d+)?)", text):
            labels.append(m.group(1).strip()[:40])
            values.append(float(m.group(2)))
            rows.append({"label": m.group(1).strip()[:40], "value": float(m.group(2))})
            if len(labels) >= 20:
                break
    else:
        raise HTTPException(400, "Upload .xlsx, .csv, or .pdf")
    if not labels:
        raise HTTPException(400, "No numeric data found")
    return JSONResponse({"labels": labels, "values": values, "rows": rows})


@app.post("/presentations/{pid}/slides/upload-temp-image")
async def upload_temp_image(
    pid: int,
    image: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    data = await image.read()
    if not data or len(data) > 8_000_000:
        raise HTTPException(400, "Invalid image")
    ext = Path(image.filename or "img.png").suffix.lower() or ".png"
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        ext = ".png"
    fname = f"tmp_{pid}_{secrets.token_hex(6)}{ext}"
    (UPLOAD_SLIDES / fname).write_bytes(data)
    return JSONResponse({"url": f"/static/uploads/slides/{fname}"})


@app.post("/presentations/{pid}/chart-from-file")
async def chart_from_file(
    pid: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    datafile: UploadFile = File(...),
    chart_type: str = Form("bar"),
    chart_animation: str = Form("float3d"),
    slide_title: str = Form("Chart"),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    raw = await datafile.read()
    fname = (datafile.filename or "").lower()
    labels, values = [], []
    if fname.endswith(".xlsx") or fname.endswith(".xlsm"):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            ws = wb.active
            for row in ws.iter_rows(values_only=True):
                if not row or row[0] is None:
                    continue
                lab = str(row[0]).strip()
                try:
                    val = float(row[1]) if len(row) > 1 and row[1] is not None else None
                except (TypeError, ValueError):
                    continue
                if lab and val is not None:
                    labels.append(lab[:40])
                    values.append(val)
        except Exception as e:
            raise HTTPException(400, f"Excel read error: {e}")
    elif fname.endswith(".csv") or fname.endswith(".txt"):
        text = raw.decode("utf-8", errors="ignore")
        for line in text.splitlines():
            parts = [x.strip() for x in line.replace(";", ",").split(",")]
            if len(parts) >= 2:
                try:
                    values.append(float(parts[1]))
                    labels.append(parts[0][:40])
                except ValueError:
                    continue
    elif fname.endswith(".pdf"):
        text = extract_text_from_upload(fname, raw)
        for m in re.finditer(r"([A-Za-z][A-Za-z0-9 /%\-]{1,30})\s*[:\|-]?\s*(\d+(?:\.\d+)?)", text):
            labels.append(m.group(1).strip()[:40])
            values.append(float(m.group(2)))
            if len(labels) >= 12:
                break
    else:
        raise HTTPException(400, "Upload .xlsx, .csv, or .pdf")
    if not labels:
        raise HTTPException(400, "No numeric data found in file")
    chart_data = ",".join(f"{l}:{v}" for l, v in zip(labels, values))
    n = len(session.exec(select(Slide).where(Slide.presentation_id == pid)).all())
    session.add(Slide(
        presentation_id=pid, position=n, title=slide_title or "Chart",
        body="Data visualisation", extra_data=chart_data,
        chart_type=chart_type or "bar", chart_data=chart_data,
        animation_in=chart_animation or "float3d", layout_style="chart",
        icon_name="chart", pattern="mesh_indigo",
    ))
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit", status_code=303)




@app.get("/presentations/{pid}/ppt-scripts", response_class=HTMLResponse)
async def ppt_scripts_page(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    """Per-slide structure + Eleon autoplay speech scripts (preserves PPT form)."""
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(
        select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)
    ).all()
    try:
        return templates.TemplateResponse("presenter/ppt_scripts.html", {
            "request": request, "user": user, "presentation": p, "slides": slides,
        })
    except Exception as e:
        print("ppt_scripts template:", e)
        # inline fallback
        rows = []
        for s in slides:
            rows.append(f"""
            <div class="rounded-xl border border-white/10 p-4 mb-4 bg-slate-900/50">
              <h2 class="text-teal-300 font-bold">#{s.position+1} {s.title or ''}</h2>
              <pre class="text-xs text-slate-400 whitespace-pre-wrap mt-2 max-h-24 overflow-auto">{(s.body or '')[:500]}</pre>
              <form method="post" action="/presentations/{pid}/ppt-scripts/{s.id}" class="mt-3 space-y-2">
                <label class="text-xs text-slate-400">Eleon speak script (autoplay)</label>
                <textarea name="notes" rows="3" class="w-full rounded-lg p-2 text-slate-900 text-sm">{s.notes or ''}</textarea>
                <button class="rounded-lg bg-teal-600 px-3 py-1.5 text-sm font-bold">Save script</button>
              </form>
            </div>""")
        html = f"""<!DOCTYPE html><html><head><meta charset=utf-8><script src="https://cdn.tailwindcss.com"></script>
        <title>Scripts — {p.title}</title></head>
        <body class="bg-slate-950 text-white p-6 max-w-3xl mx-auto">
        <a href="/presentations/{pid}/edit" class="text-teal-300">← Editor</a>
        <h1 class="text-2xl font-bold mt-3">Eleon speech scripts</h1>
        <p class="text-slate-400 text-sm mb-4">Structure from your PPT is kept. Write what Eleon should say on autoplay for each slide.</p>
        {''.join(rows) if rows else '<p>No slides yet. Import a PPT first.</p>'}
        <a class="inline-block mt-4 rounded-xl bg-teal-600 px-4 py-2 font-bold" href="/presentations/{pid}/present">Present</a>
        </body></html>"""
        return HTMLResponse(html)


@app.post("/presentations/{pid}/ppt-scripts/{sid}")
async def ppt_scripts_save(
    pid: int, sid: int,
    notes: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    s = session.get(Slide, sid)
    if not p or not s or p.owner_id != user.id or s.presentation_id != pid:
        raise HTTPException(404)
    s.notes = notes or ""
    p.updated_at = datetime.utcnow()
    session.add(s)
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/ppt-scripts?saved={sid}", status_code=303)


@app.get("/presentations/{pid}/present", response_class=HTMLResponse)
async def present_mode(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(
        select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)
    ).all()
    # Ensure new optional fields exist for template (older DB rows)
    for s in slides:
        if not getattr(s, "font_family", None):
            try: s.font_family = "Inter"
            except Exception: pass
        if not getattr(s, "font_size", None):
            try: s.font_size = "md"
            except Exception: pass
        if not getattr(s, "font_color", None):
            try: s.font_color = "#e2e8f0"
            except Exception: pass
        if not getattr(s, "backdrop_style", None):
            try: s.backdrop_style = "none"
            except Exception: pass
        if not getattr(s, "chart_effect", None):
            try: s.chart_effect = "grow"
            except Exception: pass
        if getattr(s, "show_data_table", None) is None:
            try: s.show_data_table = False
            except Exception: pass
        if not getattr(s, "chart_label_mode", None):
            try: s.chart_label_mode = "outside"
            except Exception: pass
        if not getattr(s, "images_json", None) and getattr(s, "image_path", None):
            try:
                import json as _json
                s.images_json = _json.dumps([s.image_path])
            except Exception: pass
    
    return _safe_template("presenter/present.html", {
        "request": request, "user": user, "presentation": p, "slides": slides, "shared": False,
    })



@app.post("/presentations/{pid}/upload-original-pptx")
async def upload_original_pptx(
    pid: int,
    file: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Store original PPT/PPTX for direct presentation only (no slide extraction)."""
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    fname = (file.filename or "deck.pptx").lower()
    if not fname.endswith((".pptx", ".ppt")):
        raise HTTPException(400, "Please upload a .pptx or .ppt file")
    data = await file.read()
    if len(data) > 40_000_000:
        raise HTTPException(400, "File too large (max 40MB)")
    ppt_name = f"orig_{pid}_{secrets.token_hex(4)}.pptx"
    (UPLOAD_SLIDES / ppt_name).write_bytes(data)
    p.original_pptx_path = f"/static/uploads/slides/{ppt_name}"
    try:
        from app.pptx_structure import extract_pptx_structure, scripts_to_json
        scripts = extract_pptx_structure(data)
        p.original_pptx_scripts = scripts_to_json(scripts)
    except Exception as e:
        print("structure extract:", e)
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/original-scripts", status_code=303)



@app.get("/presentations/{pid}/original-scripts", response_class=HTMLResponse)
async def original_scripts_page(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    from app.pptx_structure import scripts_from_json
    scripts = scripts_from_json(getattr(p, "original_pptx_scripts", None))
    try:
        return templates.TemplateResponse("presenter/original_scripts.html", {
            "request": request, "user": user, "presentation": p, "scripts": scripts,
        })
    except Exception as e:
        print("scripts page template:", e)
        return _safe_template("presenter/original_scripts.html", {
            "request": request, "user": user, "presentation": p, "scripts": scripts,
        })


@app.post("/presentations/{pid}/original-scripts")
async def save_original_scripts(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    form = await request.form()
    try:
        count = int(form.get("count") or 0)
    except Exception:
        count = 0
    scripts = []
    for i in range(count):
        scripts.append({
            "index": i,
            "title": (form.get(f"title_{i}") or f"Slide {i+1}")[:200],
            "body": (form.get(f"body_{i}") or "")[:4000],
            "notes": "",
            "eleon_speak": (form.get(f"speak_{i}") or "")[:2000],
            "transition": (form.get(f"transition_{i}") or "fromRight")[:40],
            "motion": (form.get(f"motion_{i}") or "grow")[:20],
        })
    from app.pptx_structure import scripts_to_json
    p.original_pptx_scripts = scripts_to_json(scripts)
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/original-scripts", status_code=303)


@app.get("/presentations/{pid}/present-original-live", response_class=HTMLResponse)
async def present_original_live(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    from app.pptx_structure import scripts_from_json
    import json as _json
    scripts = scripts_from_json(getattr(p, "original_pptx_scripts", None))
    if not scripts:
        raise HTTPException(400, "No original PPT structure. Use Store PPT first.")
    scripts_json = _json.dumps(scripts, ensure_ascii=False)
    try:
        return templates.TemplateResponse("presenter/present_original_live.html", {
            "request": request, "user": user, "presentation": p, "scripts_json": scripts_json,
        })
    except Exception as e:
        print("live present template:", e)
        # inline dramatic minimal
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset=utf-8>
<title>Dramatic present</title><script src="https://cdn.tailwindcss.com"></script>
<style>
.card{{max-width:900px;margin:2rem auto;padding:2rem;border-radius:1.5rem;background:#0f172a;border:1px solid #14b8a6;}}
.anim{{animation:in .8s ease both}}@keyframes in{{from{{opacity:0;transform:translateX(60px)}}to{{opacity:1;transform:none}}}}
</style></head><body class="bg-slate-950 text-white p-4">
<button onclick="prev()" class="px-3 py-1 bg-slate-700 rounded">Prev</button>
<button onclick="next()" class="px-3 py-1 bg-teal-600 rounded">Next</button>
<button onclick="toggleAuto()" id="ba" class="px-3 py-1 bg-violet-700 rounded">Autoplay</button>
<span id="pos"></span>
<div id="c" class="card anim"></div>
<script>
const S={scripts_json}; let i=0,auto=false;
function render(){{const s=S[i]||{{}};document.getElementById('pos').textContent=(i+1)+'/'+S.length;
document.getElementById('c').className='card anim';
document.getElementById('c').innerHTML='<h1 class="text-3xl font-black text-teal-300">'+(s.title||'')+'</h1><pre class="mt-4 whitespace-pre-wrap">'+(s.body||'')+'</pre>';
if(auto){{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(s.eleon_speak||s.title||'');u.onend=()=>{{if(auto&&i<S.length-1){{i++;render();}}}};speechSynthesis.speak(u);}}
}}
function next(){{if(i<S.length-1){{i++;render();}}}}
function prev(){{if(i>0){{i--;render();}}}}
function toggleAuto(){{auto=!auto;document.getElementById('ba').textContent=auto?'Stop':'Autoplay';if(auto)render();else speechSynthesis.cancel();}}
render();
</script></body></html>""")



@app.get("/presentations/{pid}/present-original", response_class=HTMLResponse)
async def present_original_pptx(
    pid: int, request: Request,
    user: User = Depends(require_user), session: Session = Depends(get_session),
):
    """Present the uploaded PPT/PPTX directly — no analysis. Download or keep on device."""
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    path = getattr(p, "original_pptx_path", None)
    # Prefer studio + live present flow when structure exists
    scripts_raw = getattr(p, "original_pptx_scripts", None)
    if scripts_raw:
        return RedirectResponse(f"/presentations/{pid}/original-scripts", status_code=303)

    if not path:
        raise HTTPException(404, "No original PPT uploaded for this presentation. Import a .pptx first.")
    ctx = {"request": request, "user": user, "presentation": p, "pptx_url": path}
    try:
        return _safe_template("presenter/present_original.html", ctx)
    except Exception as e:
        print("present_original template missing, using inline:", e)
        # Inline fallback so deploy never 500s if file omitted from upload
        title = (p.title or "Presentation").replace("<", "")
        pptx_url = path
        body = f"""<!DOCTYPE html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Original PPT — {title}</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="min-h-screen bg-slate-950 text-slate-100 p-6">
<a class="text-teal-300 text-sm" href="/presentations/{pid}/edit">← Editor</a>
<h1 class="text-2xl font-bold mt-3">{title} · Original PPT</h1>
<p class="text-slate-400 mt-2 text-sm">Direct file — no conversion. Download and open in PowerPoint / LibreOffice.</p>
<p class="text-xs text-slate-500 mt-2 break-all">{pptx_url}</p>
<div class="mt-6 flex flex-wrap gap-3">
  <a class="rounded-xl bg-teal-600 px-5 py-2.5 font-bold" href="{pptx_url}" download>Download PPTX</a>
  <a class="rounded-xl border border-white/20 px-5 py-2.5" href="{pptx_url}" target="_blank">Open file</a>
  <a class="rounded-xl bg-indigo-600 px-5 py-2.5 font-bold" href="/presentations/{pid}/present">Eleon slides</a>
</div>
</body></html>"""
        return HTMLResponse(body)



@app.post("/api/eleon/ask")
async def eleon_ask(
    request: Request,
    session: Session = Depends(get_session),
):
    """Eleon probe: navigate, enhance, Q&A from all loaded slide information."""
    # Allow shared viewers without full user cookie for Q&A only
    user = user_from_request(request, session)
    try:
        data = await request.json()
    except Exception:
        data = {}
    q = (data.get("question") or data.get("q") or "").strip()
    pid = data.get("presentation_id")
    idx = int(data.get("slide_index") or 0)
    phase = (data.get("phase") or "present").lower()  # present | closing_qa
    ql = q.lower().strip()

    slides = []
    if pid:
        p = session.get(Presentation, int(pid))
        if p:
            if user and p.owner_id == user.id:
                pass
            elif p.share_token:
                pass
            elif not user:
                return JSONResponse({"ok": False, "speak": "Please sign in."})
            slides = session.exec(
                select(Slide).where(Slide.presentation_id == int(pid)).order_by(Slide.position)
            ).all()

    # Full corpus for Q&A
    corpus_parts = []
    for i, s in enumerate(slides):
        corpus_parts.append(f"Slide {i+1}: {s.title}\n{s.body}\n{s.extra_data}\n{s.notes}")
    full_text = "\n\n".join(corpus_parts).lower()

    def outside_knowledge():
        return JSONResponse({
            "ok": True,
            "action": "outside",
            "speak": "Sorry I can't help with that, however, my partner can.",
            "go_off": False,
        })

    # Closing phase — only Q&A, no nav required
    if phase == "closing_qa":
        if not q:
            return JSONResponse({"ok": True, "action": "speak", "speak": "Any questions?"})
        words = [w for w in re.findall(r"[a-z0-9']{3,}", ql)]
        score = sum(1 for w in words if w in full_text)
        if score < 1 or not slides:
            return outside_knowledge()
        best_i, best_score = 0, -1
        for i, s in enumerate(slides):
            text = f"{s.title} {s.body} {s.extra_data} {s.notes}".lower()
            sc = sum(1 for w in words if w in text)
            if sc > best_score:
                best_score, best_i = sc, i
        s = slides[best_i]
        ans = f"{(s.extra_data or s.body or s.title)[:450]}"
        return JSONResponse({"ok": True, "action": "speak", "speak": ans, "go_off": False})

    if not q:
        return JSONResponse({"ok": True, "action": "speak", "speak": "How may I help you?"})

    # Voice / text commands
    if ql in ("next", "next slide", "go next", "forward"):
        return JSONResponse({"ok": True, "action": "next", "speak": "Moving to the next slide."})
    if ql in ("previous", "prev", "back", "go back", "previous slide"):
        return JSONResponse({"ok": True, "action": "prev", "speak": "Going to the previous slide."})
    if ql in ("stay", "stay here", "current", "this slide"):
        return JSONResponse({"ok": True, "action": "stay", "speak": "Staying on this slide."})
    if ql in ("stop", "eleon stop", "pause", "hold"):
        return JSONResponse({"ok": True, "action": "stop", "speak": "Paused."})
    if ql in ("continue", "eleon continue", "resume"):
        return JSONResponse({"ok": True, "action": "continue", "speak": "Continuing."})
    if ql in ("start", "eleon start", "begin", "start autoplay", "start auto play"):
        return JSONResponse({"ok": True, "action": "start", "speak": "Starting presentation."})
    if ql in ("read", "read slide", "read the slide", "read this", "read this slide", "read everything", "what does this say"):
        return JSONResponse({"ok": True, "action": "read", "speak": ""})
    if ql in ("read all", "summarize", "summary", "overview"):
        return JSONResponse({"ok": True, "action": "read_all", "speak": ""})
    if "be attentive" in ql or ql in ("listen", "listen up", "pay attention"):
        return JSONResponse({"ok": True, "action": "listen", "speak": "I am attentive and listening."})
    if "thank you" in ql and "attention" in ql:
        return JSONResponse({"ok": True, "action": "closing", "speak": "Thank you for your attention. Any questions?"})
    if ql in ("end", "end presentation", "finish", "close presentation", "conclude"):
        return JSONResponse({"ok": True, "action": "closing", "speak": "Thank you for your attention. Any questions?"})
    if ql.startswith("go to slide") or (ql.startswith("slide ") and any(x.isdigit() for x in ql.split())):
        nums = [int(x) for x in ql.split() if x.isdigit()]
        if nums:
            return JSONResponse({"ok": True, "action": "goto", "index": max(0, nums[0]-1), "speak": f"Opening slide {nums[0]}."})
    # enhance commands
    enhance_map = {
        "make it teal": "accent_teal", "teal accent": "accent_teal",
        "gold accent": "accent_gold", "violet accent": "accent_violet",
        "dark background": "bg_dark", "indigo background": "bg_indigo",
        "zoom animation": "anim_zoom", "fade animation": "anim_fade",
        "slide animation": "anim_slide", "flip animation": "anim_flip",
    }
    for phrase, act in enhance_map.items():
        if phrase in ql:
            return JSONResponse({"ok": True, "action": "enhance", "enhance": act, "speak": f"Enhancing slide: {phrase}."})

    if "animation" in ql and ("in" in ql or "entrance" in ql):
        for anim in ("fade", "slideleft", "slideup", "zoom", "flip"):
            if anim in ql.replace(" ", ""):
                val = "slideLeft" if anim == "slideleft" else ("slideUp" if anim == "slideup" else anim)
                return JSONResponse({"ok": True, "action": "set_anim_in", "value": val, "speak": f"Entrance set to {val}."})
    if "animation" in ql and ("out" in ql or "exit" in ql):
        for anim in ("fade", "slideleft", "slideup", "zoom", "flip"):
            if anim in ql.replace(" ", ""):
                val = "slideLeft" if anim == "slideleft" else ("slideUp" if anim == "slideup" else anim)
                return JSONResponse({"ok": True, "action": "set_anim_out", "value": val, "speak": f"Exit set to {val}."})

    if not slides:
        return JSONResponse({"ok": True, "action": "speak", "speak": "No presentation data loaded yet."})

    words = [w for w in re.findall(r"[a-z0-9']{3,}", ql) if w not in ("what", "the", "and", "for", "about", "tell", "please", "from", "eleon")]
    best_i, best_score = idx, -1
    for i, s in enumerate(slides):
        text = f"{s.title} {s.body} {s.extra_data} {s.notes}".lower()
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best_score, best_i = score, i
    if best_score <= 0:
        return outside_knowledge()
    s = slides[best_i]
    ans = f"{s.title}. {(s.extra_data or s.body or '')[:420]}"
    return JSONResponse({
        "ok": True,
        "action": "goto" if best_i != idx else "speak",
        "index": best_i,
        "speak": ans,
        "go_off": False,
    })



@app.post("/presentations/{pid}/import-document")
async def import_document(
    pid: int,
    document: UploadFile = File(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    data = await document.read()
    if len(data) > 15_000_000:
        raise HTTPException(400, "File too large (max 15MB)")
    fname = (document.filename or "doc.txt").lower()
    if fname.endswith((".pptx", ".ppt")):
        # always store original for direct present
        try:
            ppt_name = f"orig_{pid}_{secrets.token_hex(4)}.pptx"
            ppt_dest = UPLOAD_SLIDES / ppt_name
            ppt_dest.write_bytes(data)
            p.original_pptx_path = f"/static/uploads/slides/{ppt_name}"
            try:
                from app.pptx_structure import extract_pptx_structure, scripts_to_json
                p.original_pptx_scripts = scripts_to_json(extract_pptx_structure(data))
            except Exception as se:
                print("structure:", se)
            session.add(p)
            session.commit()
        except Exception as e:
            print("store pptx fail", e)
        payloads = pptx_to_slide_payloads(data, max_slides=30)
    else:
        text = extract_text_from_upload(document.filename or "doc.txt", data)
        payloads = document_to_slide_payloads(text, max_slides=10)
    # replace existing slides
    old = session.exec(select(Slide).where(Slide.presentation_id == pid)).all()
    for s in old:
        session.delete(s)
    session.commit()
    for i, pl in enumerate(payloads):
        _sk = dict(
            presentation_id=pid,
            position=i,
            title=pl.get("title") or f"Slide {i+1}",
            body=pl.get("body") or "",
            extra_data=pl.get("extra_data") or "Knowsoft Eleon",
            notes=pl.get("notes") or "",
            animation_in=pl.get("animation_in") or ("zoom" if i == len(payloads) - 1 else "float3d"),
            animation_out="fade",
            pattern=pl.get("pattern") or "gradient_teal",
            icon_name=pl.get("icon_name") or "",
            layout_style=pl.get("layout_style") or "title_body",
        )
        if hasattr(Slide, "eleon_script"):
            _sk["eleon_script"] = pl.get("eleon_script") or pl.get("body") or ""
        session.add(Slide(**_sk))
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/edit?imported=1", status_code=303)


@app.post("/api/slides/{sid}/enhance")
async def enhance_slide(
    sid: int,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Apply enhancement presets from side panel or Eleon voice."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    s = session.get(Slide, sid)
    if not s:
        raise HTTPException(404)
    p = session.get(Presentation, s.presentation_id)
    if not p or p.owner_id != user.id:
        raise HTTPException(403)
    action = (data.get("action") or "").lower()
    if action == "accent_teal":
        s.accent = "#14b8a6"
    elif action == "accent_gold":
        s.accent = "#f59e0b"
    elif action == "accent_violet":
        s.accent = "#8b5cf6"
    elif action == "bg_dark":
        s.bg_color = "#0f172a"
    elif action == "bg_indigo":
        s.bg_color = "#1e1b4b"
    elif action == "bg_emerald":
        s.bg_color = "#064e3b"
    elif action == "anim_fade":
        s.animation_in, s.animation_out = "fade", "fade"
    elif action == "anim_zoom":
        s.animation_in, s.animation_out = "zoom", "fade"
    elif action == "anim_slide":
        s.animation_in, s.animation_out = "slideLeft", "slideUp"
    elif action == "anim_flip":
        s.animation_in, s.animation_out = "flip", "fade"
    elif action == "set_anim_in" and data.get("value"):
        s.animation_in = str(data["value"])
    elif action == "set_anim_out" and data.get("value"):
        s.animation_out = str(data["value"])
    elif action == "append_body" and data.get("text"):
        s.body = (s.body or "") + "\n" + str(data["text"])[:500]
    elif action == "append_extra" and data.get("text"):
        s.extra_data = (s.extra_data or "") + "\n" + str(data["text"])[:1000]
    else:
        return JSONResponse({"ok": False, "error": "unknown action"})
    p.updated_at = datetime.utcnow()
    session.add(s)
    session.add(p)
    session.commit()
    return JSONResponse({
        "ok": True,
        "slide": {
            "id": s.id, "title": s.title, "body": s.body, "extra_data": s.extra_data,
            "animation_in": s.animation_in, "animation_out": s.animation_out,
            "bg_color": s.bg_color, "accent": s.accent, "image_path": s.image_path,
        }
    })


@app.post("/presentations/{pid}/share")
async def enable_share(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    if not p.share_token:
        p.share_token = secrets.token_urlsafe(12)
    session.add(p)
    session.commit()
    return JSONResponse({"ok": True, "token": p.share_token, "url": f"/share/{p.share_token}"})


@app.get("/share/{token}", response_class=HTMLResponse)
async def shared_present(token: str, request: Request, session: Session = Depends(get_session)):
    p = session.exec(select(Presentation).where(Presentation.share_token == token)).first()
    if not p:
        raise HTTPException(404, "Link not found")
    slides = session.exec(select(Slide).where(Slide.presentation_id == p.id).order_by(Slide.position)).all()
    # guest present — limited eleon (read-only Q&A)
    return _safe_template("presenter/present.html", {
        "request": request, "user": None, "presentation": p, "slides": slides, "shared": True,
    })


@app.get("/presentations/{pid}/export.pdf")
async def export_pdf(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)).all()
    from reportlab.lib.pagesizes import landscape, A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(A4))
    w, h = landscape(A4)
    for i, s in enumerate(slides):
        c.setFillColorRGB(0.06, 0.09, 0.16)
        c.rect(0, 0, w, h, fill=1, stroke=0)
        c.setFillColorRGB(0.2, 0.9, 0.75)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(30, h - 40, f"Eleon · {p.title} · Slide {i+1}")
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 28)
        c.drawString(30, h - 90, (s.title or "Slide")[:80])
        c.setFont("Helvetica", 14)
        y = h - 130
        for line in (s.body or "").splitlines()[:18]:
            c.drawString(30, y, line[:100])
            y -= 20
        c.showPage()
    c.save()
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="eleon_{pid}.pdf"'
    })




@app.post("/presentations/{pid}/translate")
async def translate_presentation(
    pid: int,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Translate all slide text into target language (MyMemory free API)."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    lang = (data.get("lang") or "es").strip().lower()[:5]
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)).all()

    def tr(text: str) -> str:
        text = (text or "").strip()
        if not text:
            return text
        try:
            import urllib.parse, urllib.request, json as _json
            # chunk long text
            out = []
            for i in range(0, len(text), 400):
                chunk = text[i:i+400]
                q = urllib.parse.quote(chunk)
                url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|{lang}"
                with urllib.request.urlopen(url, timeout=12) as resp:
                    j = _json.loads(resp.read().decode())
                out.append(j.get("responseData", {}).get("translatedText") or chunk)
            return " ".join(out)
        except Exception:
            return text

    for s in slides:
        s.title = tr(s.title)[:200]
        s.body = tr(s.body)
        s.extra_data = tr(s.extra_data)
        s.notes = tr(s.notes)
        session.add(s)
    p.title = tr(p.title)[:200]
    p.updated_at = datetime.utcnow()
    session.add(p)
    session.commit()
    return JSONResponse({"ok": True, "lang": lang, "slides": len(slides)})




@app.get("/presentations/{pid}/export-offline")
async def export_offline_html(
    pid: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    """Self-contained HTML presenter - works offline on the host device."""
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(
        select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)
    ).all()
    from app.offline_export import build_offline_html
    data = build_offline_html(p.title or "Eleon deck", slides, BASE)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="eleon_offline_{pid}.html"'},
    )


@app.get("/presentations/{pid}/export.pptx")
async def export_pptx(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    slides = session.exec(select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)).all()
    from app.pptx_export import build_pptx_bytes
    data = build_pptx_bytes(p.title or "Eleon", slides, base_dir=BASE)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="eleon_{pid}.pptx"'},
    )






def get_setting(session: Session, key: str, default: str = "") -> str:
    row = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
    return row.value if row else default

def set_setting(session: Session, key: str, value: str) -> None:
    row = session.exec(select(AppSetting).where(AppSetting.key == key)).first()
    if row:
        row.value = value
        session.add(row)
    else:
        session.add(AppSetting(key=key, value=value))
    session.commit()


# ---------- Real-time live rooms (WebSocket) ----------
class LiveHub:
    def __init__(self):
        self.rooms: dict = {}  # token -> {"host": WebSocket|None, "viewers": {vid: ws}, "names": {vid: name}}

    def room(self, token: str) -> dict:
        if token not in self.rooms:
            self.rooms[token] = {"host": None, "viewers": {}, "names": {}}
        return self.rooms[token]

    async def broadcast(self, token: str, message: dict, skip: WebSocket = None):
        import json as _json
        data = _json.dumps(message)
        room = self.rooms.get(token) or {}
        targets = []
        if room.get("host"):
            targets.append(room["host"])
        targets.extend(room.get("viewers", {}).values())
        for ws in targets:
            if ws is skip:
                continue
            try:
                await ws.send_text(data)
            except Exception:
                pass

    def online_list(self, token: str) -> list:
        room = self.rooms.get(token) or {}
        out = []
        if room.get("host"):
            out.append({"role": "host", "name": "Presenter", "id": "host"})
        for vid, name in room.get("names", {}).items():
            if vid in room.get("viewers", {}):
                out.append({"role": "viewer", "name": name, "id": str(vid)})
        return out

live_hub = LiveHub()


@app.websocket("/ws/live/{token}")
async def ws_live(websocket: WebSocket, token: str):
    await websocket.accept()
    role = websocket.query_params.get("role") or "viewer"
    vid = websocket.query_params.get("vid") or ""
    name = websocket.query_params.get("name") or "Guest"
    room = live_hub.room(token)
    from sqlmodel import Session as S
    try:
        if role == "host":
            room["host"] = websocket
        else:
            room["viewers"][vid] = websocket
            room["names"][vid] = name
        await live_hub.broadcast(token, {"type": "online", "list": live_hub.online_list(token)})
        with S(engine) as sess:
            ls = sess.exec(select(LiveSession).where(LiveSession.token == token)).first()
            if ls:
                await websocket.send_json({"type": "slide", "index": ls.current_index})
        while True:
            raw = await websocket.receive_text()
            try:
                msg = __import__("json").loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            if mtype == "slide" and role == "host":
                idx = int(msg.get("index") or 0)
                with S(engine) as sess:
                    ls = sess.exec(select(LiveSession).where(LiveSession.token == token)).first()
                    if ls:
                        ls.current_index = idx
                        sess.add(ls)
                        sess.commit()
                await live_hub.broadcast(token, {"type": "slide", "index": idx})  # all viewers
            elif mtype == "speak":
                # host Eleon speech text → all viewers TTS
                await live_hub.broadcast(token, {
                    "type": "speak",
                    "text": (msg.get("text") or "")[:4000],
                }, skip=None)
            elif mtype == "question" and role != "host":
                text = (msg.get("text") or "").strip()[:500]
                if not text:
                    continue
                with S(engine) as sess:
                    ls = sess.exec(select(LiveSession).where(LiveSession.token == token)).first()
                    if not ls:
                        continue
                    q = LiveQuestion(session_id=ls.id, viewer_name=name, text=text)
                    sess.add(q)
                    sess.commit()
                    sess.refresh(q)
                    qid = q.id
                await live_hub.broadcast(token, {
                    "type": "question",
                    "id": qid,
                    "name": name,
                    "text": text,
                })
            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})

            elif mtype == "reaction":
                await live_hub.broadcast(token, {
                    "type": "reaction",
                    "emoji": msg.get("emoji") or "👏",
                    "name": name if role != "host" else (msg.get("name") or "Presenter"),
                })
            elif mtype == "voice_chunk":
                # real-time voice: host and attendees (presenter language only — no translate)
                await live_hub.broadcast(token, {
                    "type": "voice_chunk",
                    "audio": msg.get("audio") or "",
                    "mime": msg.get("mime") or "audio/webm",
                    "from": name if role != "host" else "Presenter",
                    "role": role,
                }, skip=websocket)
            elif mtype == "end" and role == "host":
                with S(engine) as sess:
                    ls = sess.exec(select(LiveSession).where(LiveSession.token == token)).first()
                    if ls:
                        ls.is_active = False
                        sess.add(ls)
                        sess.commit()
                await live_hub.broadcast(token, {"type": "ended", "message": "The presenter has ended the session."})

            elif mtype == "admit" and role == "host":
                await live_hub.broadcast(token, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        room = live_hub.rooms.get(token)
        if room:
            if role == "host" and room.get("host") is websocket:
                room["host"] = None
            if vid in room.get("viewers", {}):
                room["viewers"].pop(vid, None)
                room["names"].pop(vid, None)
            try:
                await live_hub.broadcast(token, {"type": "online", "list": live_hub.online_list(token)})
            except Exception:
                pass



# ---------- Live join / audience ----------
@app.post("/presentations/{pid}/live/start")
async def live_start(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    # reuse active or create
    existing = session.exec(
        select(LiveSession).where(LiveSession.presentation_id == pid, LiveSession.is_active == True)
    ).first()
    if existing:
        return JSONResponse({"ok": True, "token": existing.token, "url": f"/join/{existing.token}"})
    token = secrets.token_urlsafe(10)
    ls = LiveSession(presentation_id=pid, owner_id=user.id, token=token, current_index=0)
    session.add(ls)
    session.commit()
    return JSONResponse({"ok": True, "token": token, "url": f"/join/{token}"})


@app.post("/presentations/{pid}/live/stop")
async def live_stop(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    rows = session.exec(select(LiveSession).where(LiveSession.presentation_id == pid, LiveSession.owner_id == user.id)).all()
    for ls in rows:
        ls.is_active = False
        session.add(ls)
        try:
            await live_hub.broadcast(ls.token, {"type": "ended", "message": "The presenter has ended the session."})
        except Exception:
            pass
    session.commit()
    return JSONResponse({"ok": True})


@app.post("/api/live/{token}/sync")
async def live_sync(token: str, request: Request, session: Session = Depends(get_session)):
    """Host pushes current slide index — also notifies WebSocket viewers."""
    user = user_from_request(request, session)
    ls = session.exec(select(LiveSession).where(LiveSession.token == token, LiveSession.is_active == True)).first()
    if not ls:
        raise HTTPException(404)
    if not user or ls.owner_id != user.id:
        raise HTTPException(403)
    try:
        data = await request.json()
    except Exception:
        data = {}
    idx = int(data.get("index") or 0)
    ls.current_index = idx
    session.add(ls)
    session.commit()
    try:
        await live_hub.broadcast(token, {"type": "slide", "index": idx})
    except Exception:
        pass
    return JSONResponse({"ok": True, "index": idx})


@app.get("/api/live/{token}/state")
async def live_state(token: str, session: Session = Depends(get_session)):
    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
    if not ls:
        raise HTTPException(404)
    p = session.get(Presentation, ls.presentation_id)
    slides = session.exec(select(Slide).where(Slide.presentation_id == ls.presentation_id).order_by(Slide.position)).all()
    pending = session.exec(select(LiveViewer).where(LiveViewer.session_id == ls.id, LiveViewer.status == "pending")).all()
    questions = session.exec(
        select(LiveQuestion).where(LiveQuestion.session_id == ls.id, LiveQuestion.answered == False).order_by(LiveQuestion.created_at)
    ).all()
    return JSONResponse({
        "ok": True,
        "active": ls.is_active,
        "index": ls.current_index,
        "title": p.title if p else "",
        "pending": [{"id": v.id, "name": v.name} for v in pending],
        "questions": [{"id": q.id, "name": q.viewer_name, "text": q.text} for q in questions],
        "slides": [
            {
                "title": s.title, "body": s.body, "extra": s.extra_data, "notes": s.notes,
                "image": s.image_path, "onlineImage": s.online_image_url,
                "animIn": s.animation_in, "bg": s.bg_color, "accent": s.accent,
                "layout": s.layout_style, "icon": s.icon_name, "chartType": s.chart_type,
                "chartData": s.chart_data, "pattern": getattr(s, "pattern", None) or "gradient_teal",
                "imageStyle": getattr(s, "image_style", None) or "frame",
            }
            for s in slides
        ],
    })


@app.get("/join/{token}", response_class=HTMLResponse)
async def join_page(token: str, request: Request, session: Session = Depends(get_session)):
    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
    dl = get_setting(session, "eleon_download_url", "https://knowsoftconsult.com")
    android = get_setting(session, "eleon_android_url", "") or dl
    windows = get_setting(session, "eleon_windows_url", "") or dl
    if not ls or not ls.is_active:
        return templates.TemplateResponse("presenter/join_expired.html", {
            "request": request, "token": token,
            "download_url": dl, "android_url": android, "windows_url": windows,
        })
    p = session.get(Presentation, ls.presentation_id)
    return templates.TemplateResponse("presenter/join.html", {
        "request": request, "token": token, "presentation": p, "live": ls,
        "download_url": dl, "android_url": android, "windows_url": windows,
    })


@app.post("/join/{token}")
async def join_request(token: str, name: str = Form(...), session: Session = Depends(get_session)):
    ls = session.exec(select(LiveSession).where(LiveSession.token == token, LiveSession.is_active == True)).first()
    if not ls:
        raise HTTPException(404)
    v = LiveViewer(session_id=ls.id, name=(name or "Guest").strip()[:80], status="pending")
    session.add(v)
    session.commit()
    session.refresh(v)
    # notify host rooms immediately (best-effort)
    try:
        import asyncio
        asyncio.get_event_loop().create_task(hub.broadcast(token, {
            "type": "join_request", "vid": v.id, "name": v.name
        }))
    except Exception:
        pass
    return RedirectResponse(f"/join/{token}/wait?vid={v.id}", status_code=303)


@app.get("/join/{token}/wait", response_class=HTMLResponse)
async def join_wait(token: str, request: Request, session: Session = Depends(get_session)):
    vid = request.query_params.get("vid")
    return templates.TemplateResponse("presenter/join_wait.html", {
        "request": request, "token": token, "vid": vid,
    })


@app.get("/api/join/{token}/viewer/{vid}")
async def join_viewer_status(token: str, vid: int, session: Session = Depends(get_session)):
    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
    if not ls:
        raise HTTPException(404)
    v = session.get(LiveViewer, vid)
    if not v or v.session_id != ls.id:
        raise HTTPException(404)
    return JSONResponse({"ok": True, "status": v.status, "name": v.name, "index": ls.current_index, "active": ls.is_active})


@app.post("/api/live/{token}/admit/{vid}")
async def live_admit(token: str, vid: int, request: Request, session: Session = Depends(get_session)):
    user = user_from_request(request, session)
    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
    if not ls or not user or ls.owner_id != user.id:
        raise HTTPException(403)
    v = session.get(LiveViewer, vid)
    if not v or v.session_id != ls.id:
        raise HTTPException(404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    v.status = "admitted" if data.get("admit", True) else "denied"
    session.add(v)
    session.commit()
    return JSONResponse({"ok": True, "status": v.status})


@app.get("/join/{token}/watch", response_class=HTMLResponse)
async def join_watch(token: str, request: Request, session: Session = Depends(get_session)):
    vid = request.query_params.get("vid")
    ls = session.exec(select(LiveSession).where(LiveSession.token == token, LiveSession.is_active == True)).first()
    if not ls:
        raise HTTPException(404)
    v = session.get(LiveViewer, int(vid)) if vid else None
    if not v or v.session_id != ls.id or v.status != "admitted":
        return RedirectResponse(f"/join/{token}/wait?vid={vid}", status_code=303)
    p = session.get(Presentation, ls.presentation_id)
    return templates.TemplateResponse("presenter/join_watch.html", {
        "request": request, "token": token, "vid": vid, "viewer": v, "presentation": p,
    })


@app.post("/api/join/{token}/ask")
async def join_ask(token: str, request: Request, session: Session = Depends(get_session)):
    ls = session.exec(select(LiveSession).where(LiveSession.token == token, LiveSession.is_active == True)).first()
    if not ls:
        raise HTTPException(404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = (data.get("text") or "").strip()[:500]
    name = (data.get("name") or "Guest").strip()[:80]
    if not text:
        raise HTTPException(400, "Empty question")
    q = LiveQuestion(session_id=ls.id, viewer_name=name, text=text)
    session.add(q)
    session.commit()
    session.refresh(q)
    return JSONResponse({"ok": True, "id": q.id})


@app.post("/api/live/{token}/answer/{qid}")
async def live_answer_question(token: str, qid: int, request: Request, session: Session = Depends(get_session)):
    user = user_from_request(request, session)
    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
    if not ls or not user or ls.owner_id != user.id:
        raise HTTPException(403)
    q = session.get(LiveQuestion, qid)
    if not q or q.session_id != ls.id:
        raise HTTPException(404)
    try:
        data = await request.json()
    except Exception:
        data = {}
    answer = (data.get("answer") or "").strip()[:2000]
    q.answered = True
    q.answer = answer
    session.add(q)
    session.commit()
    # also store as QA note
    session.add(PresentationQANote(presentation_id=ls.presentation_id, question=f"{q.viewer_name}: {q.text}", answer=answer))
    session.commit()
    return JSONResponse({"ok": True})


# ---------- Evaluation & Q&A notes ----------
@app.post("/api/presentations/{pid}/qa-note")
async def save_qa_note(pid: int, request: Request, session: Session = Depends(get_session)):
    try:
        data = await request.json()
    except Exception:
        data = {}
    user = user_from_request(request, session)
    p = session.get(Presentation, pid)
    if not p:
        raise HTTPException(404)
    if user and p.owner_id != user.id and not p.share_token:
        raise HTTPException(403)
    note = PresentationQANote(
        presentation_id=pid,
        question=(data.get("question") or "")[:2000],
        answer=(data.get("answer") or "")[:4000],
    )
    session.add(note)
    session.commit()
    return JSONResponse({"ok": True, "id": note.id})


@app.get("/presentations/{pid}/qa-notes", response_class=HTMLResponse)
async def list_qa_notes(pid: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    notes = session.exec(
        select(PresentationQANote).where(PresentationQANote.presentation_id == pid).order_by(PresentationQANote.created_at.desc())
    ).all()
    return templates.TemplateResponse("presenter/qa_notes.html", {
        "request": request, "user": user, "presentation": p, "notes": notes,
    })


@app.post("/presentations/{pid}/evaluation/create")
async def create_evaluation(pid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    p = session.get(Presentation, pid)
    if not p or p.owner_id != user.id:
        raise HTTPException(404)
    token = secrets.token_urlsafe(10)
    ev = EvalSession(presentation_id=pid, owner_id=user.id, title=f"Eval — {p.title}", token=token)
    session.add(ev)
    session.commit()
    session.refresh(ev)
    # Auto questions from slides
    slides = session.exec(select(Slide).where(Slide.presentation_id == pid).order_by(Slide.position)).all()
    pos = 0
    for s in slides[:8]:
        if not (s.title or s.body):
            continue
        prompt = f"What is a key point from: {(s.title or '')[:80]}?"
        session.add(EvalQuestion(session_id=ev.id, position=pos, prompt=prompt, options="", correct_answer=""))
        pos += 1
    if pos == 0:
        session.add(EvalQuestion(session_id=ev.id, position=0, prompt="How useful was this training? (1-5)", options="1\n2\n3\n4\n5", correct_answer=""))
        session.add(EvalQuestion(session_id=ev.id, position=1, prompt="Would you recommend this session?", options="Yes\nNo\nMaybe", correct_answer=""))
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/evaluation/{ev.id}", status_code=303)


@app.post("/presentations/{pid}/evaluation/{eid}/questions")
async def add_eval_question(
    pid: int, eid: int,
    prompt: str = Form(...),
    options: str = Form(""),
    correct_answer: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    ev = session.get(EvalSession, eid)
    p = session.get(Presentation, pid)
    if not ev or not p or p.owner_id != user.id or ev.presentation_id != pid:
        raise HTTPException(404)
    n = len(session.exec(select(EvalQuestion).where(EvalQuestion.session_id == eid)).all())
    session.add(EvalQuestion(session_id=eid, position=n, prompt=prompt, options=options, correct_answer=correct_answer.strip()))
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/evaluation/{eid}", status_code=303)


@app.get("/presentations/{pid}/evaluation/{eid}", response_class=HTMLResponse)
async def evaluation_admin(pid: int, eid: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    ev = session.get(EvalSession, eid)
    p = session.get(Presentation, pid)
    if not ev or not p or p.owner_id != user.id or ev.presentation_id != pid:
        raise HTTPException(404)
    qs = session.exec(select(EvalQuestion).where(EvalQuestion.session_id == eid).order_by(EvalQuestion.position)).all()
    resps = session.exec(select(EvalResponse).where(EvalResponse.session_id == eid).order_by(EvalResponse.submitted_at.desc())).all()
    return templates.TemplateResponse("presenter/evaluation.html", {
        "request": request, "user": user, "presentation": p, "eval": ev, "questions": qs, "responses": resps,
    })


@app.get("/presentations/{pid}/evaluation/{eid}/live", response_class=HTMLResponse)
async def evaluation_live(pid: int, eid: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    ev = session.get(EvalSession, eid)
    p = session.get(Presentation, pid)
    if not ev or not p or p.owner_id != user.id:
        raise HTTPException(404)
    return templates.TemplateResponse("presenter/evaluation_live.html", {
        "request": request, "user": user, "presentation": p, "eval": ev,
    })


@app.get("/api/evaluation/{token}/responses")
async def eval_responses_public(token: str, session: Session = Depends(get_session)):
    ev = session.exec(select(EvalSession).where(EvalSession.token == token)).first()
    if not ev:
        raise HTTPException(404)
    resps = session.exec(select(EvalResponse).where(EvalResponse.session_id == ev.id).order_by(EvalResponse.submitted_at.desc())).all()
    return JSONResponse({
        "ok": True,
        "items": [{"name": r.participant_name, "email": r.participant_email, "score": r.score_pct, "at": r.submitted_at.isoformat()} for r in resps],
    })


@app.get("/api/evaluation/{eid}/results")
async def eval_results(eid: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    ev = session.get(EvalSession, eid)
    if not ev or ev.owner_id != user.id:
        raise HTTPException(404)
    resps = session.exec(select(EvalResponse).where(EvalResponse.session_id == eid)).all()
    bands = {"90-100": 0, "60-89": 0, "30-59": 0, "0-29": 0}
    for r in resps:
        s = r.score_pct
        if s >= 90: bands["90-100"] += 1
        elif s >= 60: bands["60-89"] += 1
        elif s >= 30: bands["30-59"] += 1
        else: bands["0-29"] += 1
    return JSONResponse({
        "ok": True,
        "bands": bands,
        "participants": [{"name": r.participant_name, "email": r.participant_email, "score": r.score_pct} for r in resps],
    })


@app.get("/evaluate/{token}", response_class=HTMLResponse)
async def evaluate_form(token: str, request: Request, session: Session = Depends(get_session)):
    ev = session.exec(select(EvalSession).where(EvalSession.token == token, EvalSession.is_active == True)).first()
    if not ev:
        raise HTTPException(404, "Evaluation not found or closed")
    qs = session.exec(select(EvalQuestion).where(EvalQuestion.session_id == ev.id).order_by(EvalQuestion.position)).all()
    p = session.get(Presentation, ev.presentation_id)
    return templates.TemplateResponse("presenter/evaluate_take.html", {
        "request": request, "eval": ev, "questions": qs, "presentation": p,
    })


@app.post("/evaluate/{token}")
async def evaluate_submit(token: str, request: Request, session: Session = Depends(get_session)):
    ev = session.exec(select(EvalSession).where(EvalSession.token == token, EvalSession.is_active == True)).first()
    if not ev:
        raise HTTPException(404)
    form = await request.form()
    name = (form.get("participant_name") or "Guest").strip()[:120]
    email = (form.get("participant_email") or "").strip()[:200]
    qs = session.exec(select(EvalQuestion).where(EvalQuestion.session_id == ev.id).order_by(EvalQuestion.position)).all()
    answers = {}
    scored = 0
    correct = 0
    for q in qs:
        ans = (form.get(f"q_{q.id}") or "").strip()
        answers[str(q.id)] = ans
        if q.correct_answer:
            scored += 1
            if ans.lower() == q.correct_answer.lower():
                correct += 1
    pct = (100.0 * correct / scored) if scored else 100.0
    session.add(EvalResponse(session_id=ev.id, participant_name=name, participant_email=email,
                             answers_json=__import__("json").dumps(answers), score_pct=round(pct, 1)))
    session.commit()
    return templates.TemplateResponse("presenter/evaluate_done.html", {
        "request": request, "eval": ev, "name": name, "score": round(pct, 1), "token": token,
        "allow_certificates": getattr(ev, "allow_certificates", True),
    })



@app.post("/presentations/{pid}/evaluation/{eid}/cert-policy")
async def eval_cert_policy(
    pid: int, eid: int,
    allow_certificates: str = Form("on"),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    ev = session.get(EvalSession, eid)
    p = session.get(Presentation, pid)
    if not ev or not p or p.owner_id != user.id or ev.presentation_id != pid:
        raise HTTPException(404)
    ev.allow_certificates = allow_certificates in ("on", "true", "1", "yes")
    session.add(ev)
    session.commit()
    return RedirectResponse(f"/presentations/{pid}/evaluation/{eid}", status_code=303)


@app.get("/evaluate/{token}/certificate")
async def evaluate_certificate(token: str, request: Request, session: Session = Depends(get_session)):
    name = (request.query_params.get("name") or "Participant").strip()[:120]
    ev = session.exec(select(EvalSession).where(EvalSession.token == token)).first()
    if not ev:
        raise HTTPException(404)
    # Link users need presenter permission; presenter (owner) always can open with ?host=1
    host_dl = request.query_params.get("host") == "1"
    if not host_dl and not getattr(ev, "allow_certificates", True):
        raise HTTPException(403, "Certificates are disabled for this evaluation")
    p = session.get(Presentation, ev.presentation_id)
    return templates.TemplateResponse("presenter/certificate.html", {
        "request": request, "name": name, "eval": ev, "presentation": p,
    })




@app.websocket("/ws/live/{token}")
async def ws_live(websocket: WebSocket, token: str):
    """Real-time channel: host + admitted viewers."""
    role = websocket.query_params.get("role") or "viewer"
    vid = websocket.query_params.get("vid")
    await hub.connect(token, websocket, role=role)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = __import__("json").loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")
            # Host slide / speech
            if mtype in ("slide", "speak", "speak_end", "phase"):
                if role != "host":
                    continue
                await hub.broadcast(token, msg, exclude=None)
            elif mtype == "question":
                # viewer question — persist + fan out immediately
                from sqlmodel import Session as S
                from app.database import engine
                text = (msg.get("text") or "")[:500]
                name = (msg.get("name") or "Guest")[:80]
                qid = None
                with S(engine) as session:
                    ls = session.exec(select(LiveSession).where(LiveSession.token == token)).first()
                    if ls and text:
                        q = LiveQuestion(session_id=ls.id, viewer_name=name, text=text)
                        session.add(q)
                        session.commit()
                        session.refresh(q)
                        qid = q.id
                await hub.broadcast(token, {
                    "type": "question",
                    "id": qid,
                    "name": name,
                    "text": text,
                })
            elif mtype == "admit":
                if role != "host":
                    continue
                from sqlmodel import Session as S
                from app.database import engine
                with S(engine) as session:
                    v = session.get(LiveViewer, int(msg.get("vid") or 0))
                    if v:
                        v.status = "admitted" if msg.get("admit", True) else "denied"
                        session.add(v)
                        session.commit()
                await hub.broadcast(token, {
                    "type": "admit_result",
                    "vid": msg.get("vid"),
                    "status": "admitted" if msg.get("admit", True) else "denied",
                    "name": msg.get("name") or "",
                })
            elif mtype == "answer_done":
                if role != "host":
                    continue
                await hub.broadcast(token, msg)
            elif mtype == "join_request":
                # optional notify host of new pending (DB already has viewer)
                await hub.broadcast(token, {
                    "type": "join_request",
                    "vid": msg.get("vid"),
                    "name": msg.get("name") or "Guest",
                })
            elif mtype == "ping":
                await websocket.send_text('{"type":"pong"}')
    except WebSocketDisconnect:
        await hub.disconnect(token, websocket)
    except Exception:
        await hub.disconnect(token, websocket)




@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(request: Request, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    dl = get_setting(session, "eleon_download_url", "https://knowsoftconsult.com")
    android = get_setting(session, "eleon_android_url", "")
    ios = get_setting(session, "eleon_ios_url", "")
    windows = get_setting(session, "eleon_windows_url", "")
    return templates.TemplateResponse("admin/settings.html", {
        "request": request, "user": user,
        "download_url": dl, "android_url": android, "ios_url": ios, "windows_url": windows,
    })


@app.post("/admin/settings")
async def admin_settings_save(
    request: Request,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
    download_url: str = Form(""),
    android_url: str = Form(""),
    ios_url: str = Form(""),
    windows_url: str = Form(""),
):
    set_setting(session, "eleon_download_url", download_url.strip())
    set_setting(session, "eleon_android_url", android_url.strip())
    set_setting(session, "eleon_ios_url", ios_url.strip())
    set_setting(session, "eleon_windows_url", windows_url.strip())
    return RedirectResponse("/admin/settings", status_code=303)


@app.get("/api/download-links")
async def api_download_links(session: Session = Depends(get_session)):
    return JSONResponse({
        "download_url": get_setting(session, "eleon_download_url", "https://knowsoftconsult.com"),
        "android_url": get_setting(session, "eleon_android_url", ""),
        "ios_url": get_setting(session, "eleon_ios_url", ""),
        "windows_url": get_setting(session, "eleon_windows_url", ""),
        "app_scheme": "eleon://join/",
    })


@app.post("/api/translate-text")
async def api_translate_text(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}
    text = (data.get("text") or "")[:2500]
    lang = (data.get("lang") or "es")[:5]
    if not text:
        return JSONResponse({"ok": True, "text": ""})
    try:
        import urllib.parse, urllib.request, json as _json
        out = []
        for i in range(0, len(text), 400):
            chunk = text[i:i+400]
            q = urllib.parse.quote(chunk)
            url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|{lang}"
            with urllib.request.urlopen(url, timeout=12) as resp:
                j = _json.loads(resp.read().decode())
            out.append(j.get("responseData", {}).get("translatedText") or chunk)
        return JSONResponse({"ok": True, "text": " ".join(out)})
    except Exception as e:
        return JSONResponse({"ok": False, "text": text, "error": str(e)})



# ---------- Subscription ----------
@app.get("/subscription", response_class=HTMLResponse)
async def subscription_page(request: Request, session: Session = Depends(get_session)):
    user = user_from_request(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    from app.auth import ensure_user_subscription, user_sub_active
    user = ensure_user_subscription(session, user)
    settings = session.exec(select(SubscriptionSettings)).first()
    if not settings:
        settings = SubscriptionSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    history = session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id).order_by(UserSubscription.created_at.desc())
    ).all()
    remaining = None
    if user.sub_ends_at:
        remaining = max(0, int((user.sub_ends_at - datetime.utcnow()).total_seconds()))
    return templates.TemplateResponse("presenter/subscription.html", {
        "request": request, "user": user, "settings": settings, "history": history,
        "active": user_sub_active(user), "remaining_sec": remaining,
        "expired": request.query_params.get("expired") == "1",
    })


@app.post("/subscription/request")
async def subscription_request(
    request: Request,
    plan: str = Form("monthly"),
    payment_reference: str = Form(""),
    note: str = Form(""),
    session: Session = Depends(get_session),
    evidence: UploadFile = File(None),
):
    user = user_from_request(request, session)
    if not user:
        return RedirectResponse("/login", status_code=303)
    settings = session.exec(select(SubscriptionSettings)).first() or SubscriptionSettings()
    plan = plan if plan in ("monthly", "annual") else "monthly"
    amount = settings.monthly_price if plan == "monthly" else settings.annual_price
    days = 30 if plan == "monthly" else 365
    path = None
    if evidence and getattr(evidence, "filename", None):
        data = await evidence.read()
        if data and len(data) < 5_000_000:
            ext = (evidence.filename.rsplit(".", 1)[-1] or "jpg").lower()[:4]
            name = f"pay_{user.id}_{secrets.token_hex(4)}.{ext}"
            dest = UPLOAD_SLIDES / name
            dest.write_bytes(data)
            path = f"/static/uploads/slides/{name}"
    sub = UserSubscription(
        user_id=user.id, plan=plan, amount=amount, currency=settings.currency or "NGN",
        duration_days=days, status="pending", payment_reference=payment_reference.strip()[:120],
        evidence_path=path, note=note.strip()[:500],
    )
    user.sub_status = "pending_payment"
    session.add(sub)
    session.add(user)
    session.commit()
    return RedirectResponse("/subscription?sent=1", status_code=303)


@app.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(request: Request, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    settings = session.exec(select(SubscriptionSettings)).first()
    if not settings:
        settings = SubscriptionSettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    pending = session.exec(select(UserSubscription).where(UserSubscription.status == "pending").order_by(UserSubscription.created_at.desc())).all()
    recent = session.exec(select(UserSubscription).order_by(UserSubscription.created_at.desc()).limit(50)).all()
    users = {u.id: u for u in session.exec(select(User)).all()}
    return templates.TemplateResponse("admin/subscriptions.html", {
        "request": request, "user": user, "settings": settings,
        "pending": pending, "recent": recent, "users": users,
    })


@app.post("/admin/subscriptions/settings")
async def admin_sub_settings(
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
    title: str = Form("Eleon subscription"),
    currency: str = Form("NGN"),
    monthly_price: float = Form(3000),
    annual_price: float = Form(30000),
    bank_name: str = Form(""),
    account_name: str = Form(""),
    account_number: str = Form(""),
    instructions: str = Form(""),
    other_details: str = Form(""),
):
    settings = session.exec(select(SubscriptionSettings)).first()
    if not settings:
        settings = SubscriptionSettings()
    settings.title = title
    settings.currency = currency
    settings.monthly_price = monthly_price
    settings.annual_price = annual_price
    settings.bank_name = bank_name
    settings.account_name = account_name
    settings.account_number = account_number
    settings.instructions = instructions
    settings.other_details = other_details
    settings.updated_at = datetime.utcnow()
    session.add(settings)
    session.commit()
    return RedirectResponse("/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sid}/confirm")
async def admin_sub_confirm(sid: int, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    sub = session.get(UserSubscription, sid)
    if not sub:
        raise HTTPException(404)
    target = session.get(User, sub.user_id)
    if not target:
        raise HTTPException(404)
    now = datetime.utcnow()
    base = target.sub_ends_at if target.sub_ends_at and target.sub_ends_at > now else now
    ends = base + timedelta(days=sub.duration_days or 30)
    sub.status = "active"
    sub.starts_at = now
    sub.ends_at = ends
    sub.confirmed_at = now
    sub.confirmed_by = user.id
    target.sub_status = "active"
    target.sub_plan = sub.plan
    target.sub_ends_at = ends
    session.add(sub)
    session.add(target)
    session.commit()
    return RedirectResponse("/admin/subscriptions", status_code=303)


@app.post("/admin/subscriptions/{sid}/reject")
async def admin_sub_reject(sid: int, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    sub = session.get(UserSubscription, sid)
    if not sub:
        raise HTTPException(404)
    sub.status = "rejected"
    session.add(sub)
    session.commit()
    return RedirectResponse("/admin/subscriptions", status_code=303)



# ---------- Admin ----------
@app.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    pending = session.exec(select(User).where(User.status == UserStatus.pending)).all()
    users = session.exec(select(User).order_by(User.created_at.desc())).all()
    codes = session.exec(select(LoginCode).order_by(LoginCode.created_at.desc()).limit(40)).all()
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "user": user, "pending": pending, "users": users, "codes": codes,
    })


@app.post("/admin/approve/{uid}")
async def admin_approve(uid: int, duration: str = Form("month"), user: User = Depends(require_admin), session: Session = Depends(get_session)):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(404)
    target.status = UserStatus.approved
    if not target.login_number:
        target.login_number = gen_login_number()
    days = DURATION_DAYS.get(duration, 30)
    target.access_expires_at = datetime.utcnow() + timedelta(days=days)
    session.add(target)
    session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/suspend/{uid}")
async def admin_suspend(uid: int, user: User = Depends(require_admin), session: Session = Depends(get_session)):
    target = session.get(User, uid)
    if target and target.role != UserRole.general_admin:
        target.status = UserStatus.suspended
        session.add(target)
        session.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/codes/issue")
async def issue_code(
    duration: str = Form("month"),
    notes: str = Form(""),
    email: str = Form(""),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    if duration not in DURATION_DAYS:
        duration = "month"
    code = LoginCode(
        code=gen_code(),
        duration=duration,
        notes=notes,
        issued_to_email=email.strip().lower() or None,
        created_by=user.id,
    )
    session.add(code)
    session.commit()
    return RedirectResponse("/admin?code=" + code.code, status_code=303)


@app.post("/admin/extend/{uid}")
async def extend_access(
    uid: int,
    duration: str = Form("month"),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
):
    target = session.get(User, uid)
    if not target:
        raise HTTPException(404)
    days = DURATION_DAYS.get(duration, 30)
    base = target.access_expires_at if target.access_expires_at and target.access_expires_at > datetime.utcnow() else datetime.utcnow()
    target.access_expires_at = base + timedelta(days=days)
    target.status = UserStatus.approved
    session.add(target)
    session.commit()
    return RedirectResponse("/admin", status_code=303)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
