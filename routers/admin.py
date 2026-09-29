from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import get_session
from app.models import User, UserRole, ChurchUnit, ChurchMember, WeeklyStat
from app.auth import require_roles, role_val

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _safe_role(u) -> str:
    try:
        return role_val(u.role)
    except Exception:
        return ""


def _admin_shell(title: str, body: str, email: str = "") -> HTMLResponse:
    """Self-contained admin HTML — never depends on base.html."""
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 text-slate-900 min-h-screen">
<header class="bg-white border-b border-slate-200 sticky top-0 z-10">
  <div class="max-w-5xl mx-auto px-4 h-14 flex items-center justify-between gap-3">
    <div class="font-bold text-slate-900">Knowsoft Churchgate · General Admin</div>
    <div class="flex items-center gap-3 text-sm">
      <span class="text-slate-500 hidden sm:inline">{email}</span>
      <a href="/admin/" class="text-blue-700 font-medium">Home</a>
      <a href="/admin/globals" class="text-blue-700 font-medium">Globals</a>
      <a href="/auth/change-password" class="text-slate-600">Password</a>
      <a href="/auth/logout" class="text-red-600 font-medium">Sign out</a>
    </div>
  </div>
</header>
<main class="max-w-5xl mx-auto px-4 py-8">{body}</main>
</body></html>"""
    return HTMLResponse(html)


@router.get("/", response_class=HTMLResponse)
async def admin_home(
    request: Request,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    churches, pending, subadmins = [], [], []
    note = ""
    try:
        churches = list(session.exec(select(ChurchUnit)).all())
        try:
            churches.sort(key=lambda c: c.created_at or c.id or 0, reverse=True)
        except Exception:
            pass
    except Exception as e:
        note += f" Churches load error: {e}."
        try:
            session.rollback()
        except Exception:
            pass
    try:
        users = list(session.exec(select(User)).all())
    except Exception as e:
        note += f" Users load error: {e}."
        users = []
        try:
            session.rollback()
        except Exception:
            pass

    pending = [c for c in churches if (getattr(c, "approval_status", "") or "") == "pending"]
    subadmins = [u for u in users if _safe_role(u) == "church_admin"]

    pending_html = ""
    for c in pending:
        pending_html += f"""
        <div class="p-3 mb-2 rounded-xl bg-amber-50 border border-amber-100 text-sm">
          <p class="font-medium">{c.name}</p>
          <p class="text-xs text-slate-500">{c.code} · {c.level} · {c.email or ''}</p>
          <div class="mt-2 flex flex-wrap gap-2">
            <form method="post" action="/admin/churches/{c.id}/approve"><button class="text-xs px-3 py-1.5 rounded-lg bg-teal-600 text-white">Approve</button></form>
            <form method="post" action="/admin/churches/{c.id}/reject"><button class="text-xs px-3 py-1.5 rounded-lg bg-red-100 text-red-700">Reject</button></form>
            <form method="post" action="/admin/churches/{c.id}/disapprove"><button class="text-xs px-3 py-1.5 rounded-lg bg-red-600 text-white">Disapprove</button></form>
          </div>
        </div>"""
    if not pending:
        pending_html = '<p class="text-sm text-slate-400">None pending</p>'

    church_rows = ""
    for c in churches[:50]:
        church_rows += f"""
        <li class="flex flex-wrap items-center justify-between gap-2 p-2 rounded-lg hover:bg-slate-50 text-sm border-b border-slate-100">
          <div>
            <span class="font-medium">{c.name}</span>
            <span class="text-xs text-slate-500 ml-2"><code>{c.code}</code> · {getattr(c, 'approval_status', '')}</span>
          </div>
          <div class="flex gap-2">
            <a href="/admin/churches/{c.id}/dashboard" class="text-xs px-2 py-1 rounded bg-blue-700 text-white">Dashboard</a>
            <a href="/admin/churches/{c.id}" class="text-xs px-2 py-1 rounded bg-slate-800 text-white">Edit</a>
          </div>
        </li>"""
    if not church_rows:
        church_rows = '<li class="text-slate-400 text-sm p-2">No churches yet</li>'

    sub_html = ""
    for u in subadmins:
        cc = "checked" if getattr(u, "can_create_churches", False) else ""
        ca = "checked" if getattr(u, "can_approve_members", False) else ""
        cs = "checked" if getattr(u, "can_enter_stats", False) else ""
        act = (
            f'<form method="post" action="/admin/users/{u.id}/deactivate"><button class="text-xs text-red-600">Deactivate</button></form>'
            if getattr(u, "is_active", True)
            else f'<form method="post" action="/admin/users/{u.id}/activate"><button class="text-xs text-teal-600">Activate</button></form>'
        )
        sub_html += f"""
        <li class="p-3 rounded-xl bg-slate-50 border border-slate-100 text-sm mb-3">
          <p class="font-medium">{u.full_name}</p>
          <p class="text-xs text-slate-500 mb-2">{u.email}</p>
          <form method="post" action="/admin/users/{u.id}/permissions" class="flex flex-wrap gap-3 items-center text-xs">
            <label class="flex items-center gap-1"><input type="checkbox" name="can_create_churches" value="yes" {cc}> Create churches</label>
            <label class="flex items-center gap-1"><input type="checkbox" name="can_approve_members" value="yes" {ca}> Approve members</label>
            <label class="flex items-center gap-1"><input type="checkbox" name="can_enter_stats" value="yes" {cs}> Attendance</label>
            <button class="px-3 py-1 rounded-lg bg-slate-900 text-white">Save</button>
          </form>
          <div class="mt-2">{act}</div>
        </li>"""
    if not sub_html:
        sub_html = '<p class="text-sm text-slate-400">No sub-admins yet</p>'

    note_html = f'<div class="mb-4 p-3 bg-amber-50 text-amber-800 text-sm rounded-lg">{note}</div>' if note else ""

    body = f"""
    <h1 class="text-2xl font-bold mb-1">General Admin</h1>
    <p class="text-sm text-slate-500 mb-6">Approve churches, manage sub-admins, open dashboards</p>
    {note_html}
    <p class="mb-6"><a href="/admin/globals" class="text-sm font-semibold text-blue-700 hover:underline">→ Manage all Global churches</a></p>
    <div class="grid lg:grid-cols-2 gap-6">
      <div class="space-y-6">
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h2 class="font-semibold mb-4">Pending registrations ({len(pending)})</h2>
          {pending_html}
        </div>
        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
          <h2 class="font-semibold mb-4">All churches ({len(churches)})</h2>
          <ul class="max-h-96 overflow-y-auto">{church_rows}</ul>
        </div>
      </div>
      <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
        <h2 class="font-semibold mb-2">Sub-admin permissions</h2>
        <p class="text-xs text-slate-500 mb-4">Grant create-church / approve-members rights.</p>
        <ul class="max-h-[32rem] overflow-y-auto">{sub_html}</ul>
      </div>
    </div>
    """
    return _admin_shell("General Admin – Churchgate", body, getattr(user, "email", ""))


@router.get("/globals", response_class=HTMLResponse)
async def list_global_churches(
    request: Request,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    from app.models import ChurchLevel
    try:
        globals_ = list(session.exec(
            select(ChurchUnit).where(ChurchUnit.level == ChurchLevel.global_church)
        ).all())
    except Exception:
        all_c = list(session.exec(select(ChurchUnit)).all())
        globals_ = [
            c for c in all_c
            if str(getattr(c.level, "value", c.level)).lower() in ("global", "global_church")
        ]
    rows = ""
    for g in globals_:
        st = getattr(g, "approval_status", "")
        rows += f"""
        <div class="bg-white rounded-2xl border p-5 mb-4 flex flex-wrap justify-between gap-3">
          <div>
            <p class="font-semibold text-lg">{g.name}</p>
            <p class="text-xs text-slate-500"><code>{g.code}</code> · {st} · {g.resident_pastor or ''} · {g.email or ''}</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <a href="/admin/churches/{g.id}/dashboard" class="px-3 py-1.5 rounded-lg bg-blue-700 text-white text-xs font-semibold">View dashboard</a>
            <a href="/admin/churches/{g.id}" class="px-3 py-1.5 rounded-lg border text-xs">Edit</a>
            <form method="post" action="/admin/churches/{g.id}/approve"><button class="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs">Approve</button></form>
            <form method="post" action="/admin/churches/{g.id}/disapprove" onsubmit="return confirm('Disapprove and deactivate all branches?')">
              <button class="px-3 py-1.5 rounded-lg bg-red-600 text-white text-xs">Disapprove</button>
            </form>
          </div>
        </div>"""
    if not rows:
        rows = '<p class="text-slate-500">No global churches registered yet.</p>'
    body = f"""
    <h1 class="text-2xl font-bold mb-2">Global churches</h1>
    <p class="text-sm text-slate-500 mb-6"><a href="/admin/" class="text-blue-700">← Admin home</a></p>
    {rows}
    """
    return _admin_shell("Global Churches – Admin", body, getattr(user, "email", ""))


@router.get("/churches/{church_id}", response_class=HTMLResponse)
async def view_church(
    church_id: int,
    request: Request,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    body = f"""
    <h1 class="text-xl font-bold mb-4">Edit church</h1>
    <form method="post" action="/admin/churches/{church.id}/edit" class="bg-white rounded-2xl border p-6 space-y-3 max-w-lg">
      <input name="name" value="{church.name or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Name">
      <input name="resident_pastor" value="{church.resident_pastor or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Pastor">
      <input name="address" value="{church.address or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Address">
      <input name="phone" value="{church.phone or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Phone">
      <input name="email" value="{church.email or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Email">
      <textarea name="doctrine" class="w-full px-3 py-2 border rounded-lg" rows="2" placeholder="Doctrine">{church.doctrine or ''}</textarea>
      <input name="activity_days" value="{church.activity_days or ''}" class="w-full px-3 py-2 border rounded-lg" placeholder="Activity days">
      <select name="approval_status" class="w-full px-3 py-2 border rounded-lg">
        <option value="pending" {"selected" if church.approval_status=="pending" else ""}>pending</option>
        <option value="approved" {"selected" if church.approval_status=="approved" else ""}>approved</option>
        <option value="rejected" {"selected" if church.approval_status=="rejected" else ""}>rejected</option>
      </select>
      <select name="is_active" class="w-full px-3 py-2 border rounded-lg">
        <option value="yes" {"selected" if church.is_active else ""}>Active</option>
        <option value="no" {"selected" if not church.is_active else ""}>Inactive</option>
      </select>
      <button class="w-full py-2.5 rounded-lg bg-slate-900 text-white font-semibold">Save</button>
    </form>
    <p class="mt-4 text-sm"><a href="/admin/" class="text-blue-700">← Back</a></p>
    """
    return _admin_shell(f"Edit {church.name}", body, getattr(user, "email", ""))


@router.post("/churches/{church_id}/edit")
async def edit_church(
    church_id: int,
    name: str = Form(...),
    resident_pastor: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    doctrine: str = Form(""),
    activity_days: str = Form(""),
    approval_status: str = Form("approved"),
    is_active: str = Form("yes"),
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    church.name = name.strip()
    church.resident_pastor = resident_pastor.strip() or None
    church.address = address.strip() or None
    church.phone = phone.strip() or None
    church.email = email.strip() or None
    church.doctrine = doctrine.strip() or None
    church.activity_days = activity_days.strip() or None
    church.approval_status = approval_status
    church.is_active = is_active == "yes"
    session.add(church)
    session.commit()
    return RedirectResponse(f"/admin/churches/{church_id}", status_code=303)


@router.post("/churches/{church_id}/approve")
async def approve_church(
    church_id: int,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    church.approval_status = "approved"
    church.is_active = True
    session.add(church)
    for admin in session.exec(select(User).where(User.church_id == church_id)).all():
        if _safe_role(admin) in ("church_admin", "data_officer"):
            admin.is_active = True
            try:
                admin.can_approve_members = True
                admin.can_create_churches = True
            except Exception:
                pass
            session.add(admin)
    session.commit()
    return RedirectResponse("/admin/", status_code=303)


@router.post("/churches/{church_id}/reject")
async def reject_church(
    church_id: int,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    church.approval_status = "rejected"
    session.add(church)
    session.commit()
    return RedirectResponse("/admin/", status_code=303)


@router.post("/churches/{church_id}/disapprove")
async def disapprove_church(
    church_id: int,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    ids = [church.id]
    queue = [church.id]
    while queue:
        pid = queue.pop(0)
        for k in session.exec(select(ChurchUnit).where(ChurchUnit.parent_id == pid)).all():
            ids.append(k.id)
            queue.append(k.id)
    for cid in ids:
        unit = session.get(ChurchUnit, cid)
        if unit:
            unit.approval_status = "rejected"
            unit.is_active = False
            session.add(unit)
        for u in session.exec(select(User).where(User.church_id == cid)).all():
            if _safe_role(u) != "general_admin":
                u.is_active = False
                session.add(u)
    session.commit()
    return RedirectResponse("/admin/globals", status_code=303)


@router.post("/users/{user_id}/permissions")
async def set_subadmin_permissions(
    user_id: int,
    can_create_churches: str = Form(""),
    can_approve_members: str = Form(""),
    can_enter_stats: str = Form(""),
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target or _safe_role(target) == "general_admin":
        raise HTTPException(400, "Invalid user")
    try:
        target.can_create_churches = can_create_churches == "yes"
        target.can_approve_members = can_approve_members == "yes"
        target.can_enter_stats = can_enter_stats == "yes"
    except Exception:
        pass
    session.add(target)
    session.commit()
    return RedirectResponse("/admin/", status_code=303)


@router.post("/users/{user_id}/deactivate")
async def deactivate(
    user_id: int,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target or _safe_role(target) == "general_admin":
        raise HTTPException(400, "Cannot deactivate")
    target.is_active = False
    session.add(target)
    session.commit()
    return RedirectResponse("/admin/", status_code=303)


@router.post("/users/{user_id}/activate")
async def activate(
    user_id: int,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    target = session.get(User, user_id)
    if not target:
        raise HTTPException(404)
    target.is_active = True
    session.add(target)
    session.commit()
    return RedirectResponse("/admin/", status_code=303)


@router.get("/churches/{church_id}/dashboard", response_class=HTMLResponse)
async def admin_view_church_dashboard(
    church_id: int,
    request: Request,
    user: User = Depends(require_roles(UserRole.general_admin)),
    session: Session = Depends(get_session),
):
    """Simple stats view without fragile Jinja charts."""
    church = session.get(ChurchUnit, church_id)
    if not church:
        raise HTTPException(404, "Church not found")
    ids = [church.id]
    queue = [church.id]
    while queue:
        pid = queue.pop(0)
        for k in session.exec(select(ChurchUnit).where(ChurchUnit.parent_id == pid)).all():
            ids.append(k.id)
            queue.append(k.id)
    try:
        members_count = len(list(session.exec(
            select(ChurchMember).where(
                ChurchMember.church_id.in_(ids),
                ChurchMember.approval_status == "approved",
            )
        ).all()))
    except Exception:
        members_count = 0
    children = list(session.exec(select(ChurchUnit).where(ChurchUnit.parent_id == church.id)).all())
    child_list = "".join(
        f"<li class='py-1 text-sm'>{ch.name} <code class='text-xs bg-slate-100 px-1'>{ch.code}</code></li>"
        for ch in children
    ) or "<li class='text-slate-400 text-sm'>No sub-churches</li>"
    body = f"""
    <h1 class="text-2xl font-bold mb-1">{church.name}</h1>
    <p class="text-sm text-slate-500 mb-6"><code>{church.code}</code> · {getattr(church.level, 'value', church.level)} · {church.approval_status}</p>
    <div class="grid sm:grid-cols-3 gap-4 mb-8">
      <div class="bg-white rounded-xl border p-5"><p class="text-xs text-slate-500 uppercase">Members (tree)</p><p class="text-3xl font-bold">{members_count}</p></div>
      <div class="bg-white rounded-xl border p-5"><p class="text-xs text-slate-500 uppercase">Sub-units</p><p class="text-3xl font-bold">{len(children)}</p></div>
      <div class="bg-white rounded-xl border p-5"><p class="text-xs text-slate-500 uppercase">Status</p><p class="text-xl font-bold capitalize">{church.approval_status}</p></div>
    </div>
    <div class="bg-white rounded-xl border p-5 mb-6">
      <h2 class="font-semibold mb-2">Sub-churches</h2>
      <ul>{child_list}</ul>
    </div>
    <p class="text-sm"><a href="/admin/" class="text-blue-700">← Admin home</a> ·
       <a href="/admin/globals" class="text-blue-700">Globals</a> ·
       <a href="/admin/churches/{church.id}" class="text-blue-700">Edit</a></p>
    """
    return _admin_shell(f"Dashboard – {church.name}", body, getattr(user, "email", ""))
