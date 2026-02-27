from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean,
    DateTime, ForeignKey, Enum, JSON
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import enum
from database import Base


# ─── ENUMS ────────────────────────────────────────────────────────────────────

class LeadStatus(str, enum.Enum):
    novo          = "Novo Lead"
    nutricao      = "Em Nutrição"
    qualificado   = "Qualificado"
    proposta      = "Proposta"
    negociacao    = "Negociação"
    fechado       = "Fechado"
    perdido       = "Perdido"

class LeadSource(str, enum.Enum):
    facebook      = "Facebook Ads"
    instagram     = "Instagram Ads"
    organico      = "Orgânico"
    indicacao     = "Indicação"
    evento        = "Evento"
    webhook       = "Webhook"
    manual        = "Manual"

class WebhookMethod(str, enum.Enum):
    POST   = "POST"
    GET    = "GET"

class WebhookEvent(str, enum.Enum):
    lead_created        = "lead.created"
    lead_updated        = "lead.updated"
    lead_stage_changed  = "lead.stage_changed"
    lead_closed         = "lead.closed"
    nurture_sent        = "nurture.sent"
    meta_lead_received  = "meta.lead_received"

class NurtureStepType(str, enum.Enum):
    email     = "email"
    whatsapp  = "whatsapp"
    wait      = "wait"
    task      = "task"


# ─── PIPELINE STAGE ───────────────────────────────────────────────────────────

class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id         = Column(Integer, primary_key=True)
    name       = Column(String(100), nullable=False)
    color      = Column(String(20), default="#4f7cff")
    order      = Column(Integer, default=0)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    leads = relationship("Lead", back_populates="stage_rel")


# ─── LEAD ─────────────────────────────────────────────────────────────────────

class Lead(Base):
    __tablename__ = "leads"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(200), nullable=False)
    email        = Column(String(200), index=True)
    phone        = Column(String(50))
    company      = Column(String(200))
    job_title    = Column(String(100))
    status       = Column(Enum(LeadStatus), default=LeadStatus.novo, index=True)
    source       = Column(Enum(LeadSource), default=LeadSource.manual)
    stage_id     = Column(Integer, ForeignKey("pipeline_stages.id"), nullable=True)
    value        = Column(Float, default=0.0)
    currency     = Column(String(10), default="BRL")
    campaign     = Column(String(200))
    ad_id        = Column(String(100))            # Meta ad reference
    form_id      = Column(String(100))            # Meta form reference
    notes        = Column(Text)
    custom_fields= Column(JSON, default={})
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    stage_rel    = relationship("PipelineStage", back_populates="leads")
    activities   = relationship("Activity", back_populates="lead", cascade="all, delete-orphan")
    nurture_enrollments = relationship("NurtureEnrollment", back_populates="lead")


# ─── ACTIVITY / HISTORY ───────────────────────────────────────────────────────

class Activity(Base):
    __tablename__ = "activities"

    id          = Column(Integer, primary_key=True)
    lead_id     = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    type        = Column(String(50))   # note, email, call, whatsapp, stage_change, webhook
    description = Column(Text)
    metadata    = Column(JSON, default={})
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="activities")


# ─── WEBHOOK ──────────────────────────────────────────────────────────────────

class Webhook(Base):
    __tablename__ = "webhooks"

    id           = Column(Integer, primary_key=True)
    name         = Column(String(200), nullable=False)
    description  = Column(Text)
    url          = Column(String(500), nullable=False)
    method       = Column(Enum(WebhookMethod), default=WebhookMethod.POST)
    secret       = Column(String(200))           # HMAC signing secret
    events       = Column(JSON, default=[])       # list of WebhookEvent strings
    headers      = Column(JSON, default={})       # custom headers
    is_active    = Column(Boolean, default=True)
    retry_count  = Column(Integer, default=3)
    timeout_sec  = Column(Integer, default=10)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id              = Column(Integer, primary_key=True)
    webhook_id      = Column(Integer, ForeignKey("webhooks.id"), nullable=False, index=True)
    event           = Column(String(100))
    payload         = Column(JSON)
    response_status = Column(Integer)
    response_body   = Column(Text)
    duration_ms     = Column(Integer)
    success         = Column(Boolean, default=False)
    attempt         = Column(Integer, default=1)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    webhook = relationship("Webhook", back_populates="deliveries")


# ─── META WEBHOOK RECEIVER ────────────────────────────────────────────────────

class MetaWebhookLog(Base):
    __tablename__ = "meta_webhook_logs"

    id          = Column(Integer, primary_key=True)
    form_id     = Column(String(100))
    ad_id       = Column(String(100))
    page_id     = Column(String(100))
    raw_payload = Column(JSON)
    processed   = Column(Boolean, default=False)
    lead_id     = Column(Integer, ForeignKey("leads.id"), nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# ─── NURTURE SEQUENCE ─────────────────────────────────────────────────────────

class NurtureSequence(Base):
    __tablename__ = "nurture_sequences"

    id          = Column(Integer, primary_key=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text)
    trigger     = Column(String(100))   # e.g. "lead.created", "lead.stage_changed:Qualificado"
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    steps       = relationship("NurtureStep", back_populates="sequence", order_by="NurtureStep.order")
    enrollments = relationship("NurtureEnrollment", back_populates="sequence")


class NurtureStep(Base):
    __tablename__ = "nurture_steps"

    id          = Column(Integer, primary_key=True)
    sequence_id = Column(Integer, ForeignKey("nurture_sequences.id"), nullable=False)
    order       = Column(Integer, default=0)
    type        = Column(Enum(NurtureStepType))
    subject     = Column(String(300))           # for email
    body        = Column(Text)
    delay_hours = Column(Integer, default=0)    # wait delay
    metadata    = Column(JSON, default={})

    sequence = relationship("NurtureSequence", back_populates="steps")


class NurtureEnrollment(Base):
    __tablename__ = "nurture_enrollments"

    id              = Column(Integer, primary_key=True)
    lead_id         = Column(Integer, ForeignKey("leads.id"), nullable=False)
    sequence_id     = Column(Integer, ForeignKey("nurture_sequences.id"), nullable=False)
    current_step    = Column(Integer, default=0)
    status          = Column(String(50), default="active")   # active, paused, completed
    enrolled_at     = Column(DateTime(timezone=True), server_default=func.now())
    next_step_at    = Column(DateTime(timezone=True))

    lead     = relationship("Lead", back_populates="nurture_enrollments")
    sequence = relationship("NurtureSequence", back_populates="enrollments")
