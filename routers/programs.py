from pathlib import Path
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from datetime import date
import uuid, shutil

from app.database import get_session
from app.models import (
    User, UserRole, ChurchUnit, ChurchLevel, SpecialProgram, ProgramPhoto,
    PhotoLike, PhotoComment, ChurchMember
)
from app.auth import require_user, require_roles

router = APIRouter(prefix="/programs", tags=["programs"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
UPLOAD = Path("app/static/uploads/programs")
UPLOAD.mkdir(parents=True, exist_ok=True)

def member_scope_church_ids(user: User, session: Session) -> set:
    """Churches whose program photos this member may see (state, group, district of their tree)."""
    ids = set()
    if user.church_id:
        ids.add(user.church_id)
    member = None
    if user.member_id:
        member = session.get(ChurchMember, user.member_id)
    if not member and user.email:
        member = session.exec(select(ChurchMember).where(ChurchMember.email == user.email)).first()
    if member:
        for cid in (member.district_church_id if hasattr(member, 'district_church_id') else None,
                    member.church_id, member.group_church_id, member.state_church_id,
                    member.country_church_id, member.global_church_id):
            if cid:
                ids.add(cid)
        # also include parent chain of district
        ch = session.get(ChurchUnit, member.church_id)
        while ch:
            ids.add(ch.id)
            if ch.parent_id:
                ch = session.get(ChurchUnit, ch.parent_id)
            else:
                break
    return ids

@router.get("/", response_class=HTMLResponse)
async def list_programs(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session)
):
    if user.role == UserRole.general_admin:
        programs = session.exec(select(SpecialProgram).order_by(SpecialProgram.created_at.desc()).limit(50)).all()
    elif user.role in (UserRole.church_admin, UserRole.data_officer):
        # own church + children broadcasts
        programs = session.exec(
            select(SpecialProgram).where(SpecialProgram.church_id == user.church_id)
            .order_by(SpecialProgram.created_at.desc())
        ).all()
        # also programs broadcast into this level from parents - simplified: show all in hierarchy
        scope = member_scope_church_ids(user, session)
        more = session.exec(select(SpecialProgram).where(SpecialProgram.church_id.in_(list(scope) or [0]))).all()
        seen = {p.id for p in programs}
        for p in more:
            if p.id not in seen:
                programs.append(p)
    else:
        scope = member_scope_church_ids(user, session)
        programs = session.exec(
            select(SpecialProgram).where(
                SpecialProgram.church_id.in_(list(scope) or [0]),
                SpecialProgram.is_active == True
            ).order_by(SpecialProgram.created_at.desc())
        ).all() if scope else []

    return templates.TemplateResponse("programs/list.html", {
        "request": request, "user": user, "programs": programs
    })

@router.get("/create", response_class=HTMLResponse)
async def create_program_page(
    request: Request,
    user: User = Depends(require_roles(UserRole.church_admin, UserRole.general_admin)),
    session: Session = Depends(get_session)
):
    church = session.get(ChurchUnit, user.church_id) if user.church_id else None
    return templates.TemplateResponse("programs/create.html", {
        "request": request, "user": user, "church": church
    })

@router.post("/create")
async def create_program(
    title: str = Form(...),
    description: str = Form(""),
    program_date: str = Form(""),
    location: str = Form(""),
    broadcast_to: str = Form("district"),
    user: User = Depends(require_roles(UserRole.church_admin, UserRole.general_admin)),
    session: Session = Depends(get_session)
):
    if not user.church_id:
        raise HTTPException(400, "No church linked")
    pd = None
    if program_date:
        try:
            pd = date.fromisoformat(program_date)
        except ValueError:
            pass
    prog = SpecialProgram(
        church_id=user.church_id,
        title=title.strip(),
        description=description.strip() or None,
        program_date=pd,
        location=location.strip() or None,
        broadcast_to=broadcast_to,
        created_by=user.id,
        is_active=True,
    )
    session.add(prog)
    session.commit()
    session.refresh(prog)
    return RedirectResponse(f"/programs/{prog.id}", status_code=303)

@router.get("/{program_id}", response_class=HTMLResponse)
async def view_program(
    program_id: int,
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session)
):
    prog = session.get(SpecialProgram, program_id)
    if not prog:
        raise HTTPException(404, "Program not found")
    photos = session.exec(select(ProgramPhoto).where(ProgramPhoto.program_id == program_id)).all()
    photo_data = []
    for ph in photos:
        likes = session.exec(select(PhotoLike).where(PhotoLike.photo_id == ph.id)).all()
        comments = session.exec(
            select(PhotoComment).where(PhotoComment.photo_id == ph.id).order_by(PhotoComment.created_at)
        ).all()
        comment_list = []
        for cm in comments:
            u = session.get(User, cm.user_id)
            comment_list.append({"body": cm.body, "user": u.full_name if u else "Member", "at": cm.created_at})
        liked = any(l.user_id == user.id for l in likes)
        photo_data.append({
            "id": ph.id, "path": ph.file_path, "caption": ph.caption,
            "likes": len(likes), "liked": liked, "comments": comment_list
        })
    church = session.get(ChurchUnit, prog.church_id)
    can_upload = user.role in (UserRole.church_admin, UserRole.general_admin)
    return templates.TemplateResponse("programs/view.html", {
        "request": request, "user": user, "program": prog, "church": church,
        "photos": photo_data, "can_upload": can_upload
    })

@router.post("/{program_id}/photo")
async def upload_photo(
    program_id: int,
    caption: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_roles(UserRole.church_admin, UserRole.general_admin)),
    session: Session = Depends(get_session)
):
    """Only church admin can upload — members cannot."""
    prog = session.get(SpecialProgram, program_id)
    if not prog:
        raise HTTPException(404, "Program not found")
    if user.role == UserRole.church_admin and prog.church_id != user.church_id:
        raise HTTPException(403, "Not your church program")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Images only")
    ext = (file.filename or "img.jpg").rsplit(".", 1)[-1].lower()
    fname = f"prog_{program_id}_{uuid.uuid4().hex[:8]}.{ext}"
    dest = UPLOAD / fname
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    ph = ProgramPhoto(
        program_id=program_id,
        file_path=f"/static/uploads/programs/{fname}",
        caption=caption.strip() or None,
        uploaded_by=user.id,
    )
    session.add(ph)
    session.commit()
    return RedirectResponse(f"/programs/{program_id}", status_code=303)

@router.get("/photo/{photo_id}/download")
async def download_photo(
    photo_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session)
):
    """Only logged-in approved members / staff can download."""
    if user.role == UserRole.member:
        member = session.get(ChurchMember, user.member_id) if user.member_id else None
        if member and member.approval_status != "approved":
            raise HTTPException(403, "Membership not approved yet")
    ph = session.get(ProgramPhoto, photo_id)
    if not ph:
        raise HTTPException(404, "Photo not found")
    path = Path("app") / ph.file_path.lstrip("/")
    if not path.exists():
        path = Path(ph.file_path.lstrip("/"))
    if not path.exists():
        # try relative from static
        path = Path("app/static/uploads/programs") / Path(ph.file_path).name
    if not path.exists():
        raise HTTPException(404, "File missing")
    return FileResponse(path, filename=path.name)

@router.post("/photo/{photo_id}/like")
async def like_photo(
    photo_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session)
):
    existing = session.exec(
        select(PhotoLike).where(PhotoLike.photo_id == photo_id, PhotoLike.user_id == user.id)
    ).first()
    if existing:
        session.delete(existing)
    else:
        session.add(PhotoLike(photo_id=photo_id, user_id=user.id))
    session.commit()
    ph = session.get(ProgramPhoto, photo_id)
    return RedirectResponse(f"/programs/{ph.program_id}" if ph else "/programs/", status_code=303)

@router.post("/photo/{photo_id}/comment")
async def comment_photo(
    photo_id: int,
    body: str = Form(...),
    user: User = Depends(require_user),
    session: Session = Depends(get_session)
):
    if not body.strip():
        raise HTTPException(400, "Empty comment")
    session.add(PhotoComment(photo_id=photo_id, user_id=user.id, body=body.strip()))
    session.commit()
    ph = session.get(ProgramPhoto, photo_id)
    return RedirectResponse(f"/programs/{ph.program_id}" if ph else "/programs/", status_code=303)
