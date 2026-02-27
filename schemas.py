from pydantic import BaseModel, EmailStr, HttpUrl, field_validator
from typing import Optional, List, Any, Dict
from datetime import datetime
from models import LeadStatus, LeadSource, WebhookMethod, WebhookEvent, NurtureStepType


# ─── PIPELINE STAGE ───────────────────────────────────────────────────────────

class PipelineStageCreate(BaseModel):
    name: str
    color: str = "#4f7cff"
    order: int = 0

class PipelineStageOut(PipelineStageCreate):
    id: int
    is_active: bool
    created_at: datetime
    class Config: from_attributes = True


# ─── LEAD ─────────────────────────────────────────────────────────────────────

class LeadCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    status: LeadStatus = LeadStatus.novo
    source: LeadSource = LeadSource.manual
    stage_id: Optional[int] = None
    value: float = 0.0
    currency: str = "BRL"
    campaign: Optional[str] = None
    ad_id: Optional[str] = None
    form_id: Optional[str] = None
    notes: Optional[str] = None
    custom_fields: Dict[str, Any] = {}

class LeadUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    status: Optional[LeadStatus] = None
    source: Optional[LeadSource] = None
    stage_id: Optional[int] = None
    value: Optional[float] = None
    campaign: Optional[str] = None
    notes: Optional[str] = None
    custom_fields: Optional[Dict[str, Any]] = None

class LeadOut(BaseModel):
    id: int
    name: str
    email: Optional[str]
    phone: Optional[str]
    company: Optional[str]
    job_title: Optional[str]
    status: LeadStatus
    source: LeadSource
    stage_id: Optional[int]
    value: float
    currency: str
    campaign: Optional[str]
    ad_id: Optional[str]
    notes: Optional[str]
    custom_fields: Dict[str, Any]
    created_at: datetime
    updated_at: Optional[datetime]
    class Config: from_attributes = True

class LeadWithActivities(LeadOut):
    activities: List["ActivityOut"] = []

class LeadStageMove(BaseModel):
    stage_id: int
    status: Optional[LeadStatus] = None


# ─── ACTIVITY ─────────────────────────────────────────────────────────────────

class ActivityCreate(BaseModel):
    type: str
    description: str
    metadata: Dict[str, Any] = {}

class ActivityOut(BaseModel):
    id: int
    lead_id: int
    type: str
    description: str
    metadata: Dict[str, Any]
    created_at: datetime
    class Config: from_attributes = True


# ─── WEBHOOK ──────────────────────────────────────────────────────────────────

class WebhookCreate(BaseModel):
    name: str
    description: Optional[str] = None
    url: str
    method: WebhookMethod = WebhookMethod.POST
    secret: Optional[str] = None
    events: List[str]                    # list of WebhookEvent values
    headers: Dict[str, str] = {}
    retry_count: int = 3
    timeout_sec: int = 10

    @field_validator("events")
    @classmethod
    def validate_events(cls, v):
        valid = {e.value for e in WebhookEvent}
        for ev in v:
            if ev not in valid:
                raise ValueError(f"Invalid event '{ev}'. Valid: {valid}")
        return v

class WebhookUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    method: Optional[WebhookMethod] = None
    secret: Optional[str] = None
    events: Optional[List[str]] = None
    headers: Optional[Dict[str, str]] = None
    is_active: Optional[bool] = None
    retry_count: Optional[int] = None
    timeout_sec: Optional[int] = None

class WebhookOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    url: str
    method: WebhookMethod
    events: List[str]
    headers: Dict[str, str]
    is_active: bool
    retry_count: int
    timeout_sec: int
    created_at: datetime
    updated_at: Optional[datetime]
    class Config: from_attributes = True

class WebhookDeliveryOut(BaseModel):
    id: int
    webhook_id: int
    event: str
    payload: Dict[str, Any]
    response_status: Optional[int]
    response_body: Optional[str]
    duration_ms: Optional[int]
    success: bool
    attempt: int
    created_at: datetime
    class Config: from_attributes = True

class WebhookTestRequest(BaseModel):
    event: str = "lead.created"
    payload: Optional[Dict[str, Any]] = None


# ─── META ─────────────────────────────────────────────────────────────────────

class MetaLeadPayload(BaseModel):
    """Raw Meta Lead Ads webhook payload"""
    object: str
    entry: List[Dict[str, Any]]


# ─── NURTURE ──────────────────────────────────────────────────────────────────

class NurtureStepCreate(BaseModel):
    type: NurtureStepType
    subject: Optional[str] = None
    body: Optional[str] = None
    delay_hours: int = 0
    metadata: Dict[str, Any] = {}

class NurtureSequenceCreate(BaseModel):
    name: str
    description: Optional[str] = None
    trigger: str
    steps: List[NurtureStepCreate] = []

class NurtureSequenceOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    trigger: str
    is_active: bool
    created_at: datetime
    class Config: from_attributes = True


# ─── PAGINATION / FILTERS ─────────────────────────────────────────────────────

class PaginatedLeads(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[LeadOut]

class LeadFilters(BaseModel):
    status: Optional[LeadStatus] = None
    source: Optional[LeadSource] = None
    stage_id: Optional[int] = None
    search: Optional[str] = None
    page: int = 1
    page_size: int = 50
