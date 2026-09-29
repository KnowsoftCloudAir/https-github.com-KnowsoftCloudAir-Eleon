"""Eleon data models — compatible with SQLModel + Pydantic v2 / Python 3.11–3.12."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column, Text
import enum


class UserRole(str, enum.Enum):
    general_admin = "general_admin"
    presenter = "presenter"


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    suspended = "suspended"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    full_name: str = Field(default="")
    hashed_password: str = Field(default="")
    role: UserRole = Field(default=UserRole.presenter)
    status: UserStatus = Field(default=UserStatus.pending)
    login_number: Optional[str] = Field(default=None, index=True, unique=True)
    access_expires_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    sub_status: str = Field(default="trial_12h")
    sub_ends_at: Optional[datetime] = Field(default=None)
    free_month_used: bool = Field(default=False)
    sub_plan: str = Field(default="trial")


class LoginCode(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)
    duration: str = Field(default="month")
    issued_to_email: Optional[str] = Field(default=None)
    issued_to_user_id: Optional[int] = Field(default=None)
    is_used: bool = Field(default=False)
    created_by: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = Field(default=None)
    notes: str = Field(default="")


class Presentation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True)
    title: str = Field(default="Untitled presentation")
    theme: str = Field(default="midnight")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    share_token: Optional[str] = Field(default=None, index=True)
    logo_path: Optional[str] = Field(default=None)
    footer_text: str = Field(default="")
    default_pattern: str = Field(default="gradient_teal")
    original_pptx_path: Optional[str] = Field(default=None)
    original_pptx_scripts: Optional[str] = Field(default=None, sa_column=Column(Text))  # JSON list of {title, body, eleon_speak}


class Slide(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: int = Field(index=True)
    position: int = Field(default=0)
    title: str = Field(default="")
    body: str = Field(default="", sa_column=Column(Text))
    extra_data: str = Field(default="", sa_column=Column(Text))
    image_path: Optional[str] = Field(default=None)
    animation_in: str = Field(default="fade")
    animation_out: str = Field(default="fade")
    bg_color: str = Field(default="#0f172a")
    accent: str = Field(default="#14b8a6")
    notes: str = Field(default="", sa_column=Column(Text))
    layout_style: str = Field(default="title_body")
    icon_name: str = Field(default="")
    chart_type: str = Field(default="")
    chart_data: str = Field(default="", sa_column=Column(Text))
    keyword_animation: bool = Field(default=True)
    word_animation: str = Field(default="fadeUp")
    online_image_url: Optional[str] = Field(default=None)
    pattern: str = Field(default="gradient_teal")
    image_style: str = Field(default="frame")
    word_emphasis: bool = Field(default=True)
    images_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    font_family: str = Field(default="Inter")
    font_size: str = Field(default="md")
    font_color: str = Field(default="#e2e8f0")
    backdrop_style: str = Field(default="none")
    chart_effect: str = Field(default="grow")
    show_data_table: bool = Field(default=False)
    chart_label_mode: str = Field(default="outside")
    eleon_script: str = Field(default="", sa_column=Column(Text))  # what Eleon speaks on autoplay


class EvalSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: int = Field(index=True)
    owner_id: int = Field(index=True)
    title: str = Field(default="Training evaluation")
    token: str = Field(index=True, unique=True)
    is_active: bool = Field(default=True)
    allow_certificates: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvalQuestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    position: int = Field(default=0)
    prompt: str = Field(default="", sa_column=Column(Text))
    options: str = Field(default="", sa_column=Column(Text))
    correct_answer: str = Field(default="")


class EvalResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    participant_name: str = Field(default="")
    participant_email: str = Field(default="")
    answers_json: str = Field(default="{}", sa_column=Column(Text))
    score_pct: float = Field(default=0.0)
    submitted_at: datetime = Field(default_factory=datetime.utcnow)


class PresentationQANote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: int = Field(index=True)
    question: str = Field(default="", sa_column=Column(Text))
    answer: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LiveSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    presentation_id: int = Field(index=True)
    owner_id: int = Field(index=True)
    token: str = Field(index=True, unique=True)
    is_active: bool = Field(default=True)
    current_index: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LiveViewer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    name: str = Field(default="Guest")
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class LiveQuestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    viewer_name: str = Field(default="Guest")
    text: str = Field(default="", sa_column=Column(Text))
    answered: bool = Field(default=False)
    answer: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AppSetting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str = Field(default="", sa_column=Column(Text))


class SubscriptionSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(default="Eleon subscription")
    currency: str = Field(default="NGN")
    monthly_price: float = Field(default=3000.0)
    annual_price: float = Field(default=30000.0)
    instructions: str = Field(
        default="Pay to the account below and upload evidence. Admin will activate your plan.",
        sa_column=Column(Text),
    )
    bank_name: str = Field(default="")
    account_name: str = Field(default="")
    account_number: str = Field(default="")
    other_details: str = Field(default="", sa_column=Column(Text))
    is_active: bool = Field(default=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSubscription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    plan: str = Field(default="monthly")
    amount: float = Field(default=0.0)
    currency: str = Field(default="NGN")
    duration_days: int = Field(default=30)
    status: str = Field(default="pending")
    payment_reference: str = Field(default="")
    evidence_path: Optional[str] = Field(default=None)
    starts_at: Optional[datetime] = Field(default=None)
    ends_at: Optional[datetime] = Field(default=None)
    confirmed_at: Optional[datetime] = Field(default=None)
    confirmed_by: Optional[int] = Field(default=None)
    note: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.utcnow)
