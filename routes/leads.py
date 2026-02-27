from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import Optional
import asyncio

from database import get_db
from models import Lead, Activity, LeadStatus, LeadSource, User
from services.auth import get_current_user
from schemas import (
    LeadCreate, LeadUpdate, LeadOut, LeadWithActivities,
    ActivityCreate, ActivityOut, PaginatedLeads, LeadStageMove
)
from services.webhook_dispatcher import dispatch_event

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("/", response_model=PaginatedLeads)
def list_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, le=200),
    status: Optional[str] = None,
    source: Optional[str] = None,
    stage_id: Optional[int] = None,
    search: Optional[str] = None,
):
    q = db.query(Lead).filter(Lead.is_active == True)
    # Admin vê todos; vendedor só vê os próprios
    if current_user.role != "admin":
        q = q.filter(Lead.owner_id == current_user.id)

    if status:
        q = q.filter(Lead.status == status)
    if source:
        q = q.filter(Lead.source == source)
    if stage_id:
        q = q.filter(Lead.stage_id == stage_id)
    if search:
        term = f"%{search}%"
        q = q.filter(
            or_(
                Lead.name.ilike(term),
                Lead.email.ilike(term),
                Lead.company.ilike(term),
                Lead.phone.ilike(term),
            )
        )

    total = q.count()
    items = (
        q.order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return PaginatedLeads(total=total, page=page, page_size=page_size, items=items)


@router.post("/", response_model=LeadOut, status_code=201)
async def create_lead(
    payload: LeadCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = Lead(**payload.model_dump(), owner_id=current_user.id)
    db.add(lead)
    db.flush()

    # Log activity
    activity = Activity(
        lead_id=lead.id,
        type="created",
        description=f"Lead criado via {lead.source.value}",
    )
    db.add(activity)
    db.commit()
    db.refresh(lead)

    # Dispatch webhook event in background
    background_tasks.add_task(
        asyncio.run,
        dispatch_event(db, "lead.created", _lead_to_dict(lead), lead.id)
    )

    return lead


@router.get("/{lead_id}", response_model=LeadWithActivities)
def get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_active == True).first()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    return lead


@router.patch("/{lead_id}", response_model=LeadOut)
async def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_active == True).first()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")

    old_status = lead.status
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(lead, k, v)

    db.add(Activity(
        lead_id=lead.id,
        type="updated",
        description="Lead atualizado",
        extra_data=updates,
    ))
    db.commit()
    db.refresh(lead)

    background_tasks.add_task(
        asyncio.run,
        dispatch_event(db, "lead.updated", _lead_to_dict(lead), lead.id)
    )

    if old_status != lead.status:
        background_tasks.add_task(
            asyncio.run,
            dispatch_event(db, "lead.stage_changed", {
                "lead": _lead_to_dict(lead),
                "from_status": old_status.value,
                "to_status": lead.status.value,
            }, lead.id)
        )
        if lead.status == LeadStatus.fechado:
            background_tasks.add_task(
                asyncio.run,
                dispatch_event(db, "lead.closed", _lead_to_dict(lead), lead.id)
            )

    return lead


@router.post("/{lead_id}/move", response_model=LeadOut)
async def move_lead_stage(
    lead_id: int,
    payload: LeadStageMove,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")

    old_status = lead.status
    lead.stage_id = payload.stage_id
    if payload.status:
        lead.status = payload.status

    db.add(Activity(
        lead_id=lead.id,
        type="stage_change",
        description=f"Movido para etapa {payload.stage_id}",
    ))
    db.commit()
    db.refresh(lead)

    background_tasks.add_task(
        asyncio.run,
        dispatch_event(db, "lead.stage_changed", {
            "lead": _lead_to_dict(lead),
            "from_status": old_status.value,
            "to_status": lead.status.value,
        }, lead.id)
    )

    return lead


@router.delete("/{lead_id}", status_code=204)
def delete_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")
    lead.is_active = False
    db.commit()


@router.post("/{lead_id}/activities", response_model=ActivityOut, status_code=201)
def add_activity(
    lead_id: int,
    payload: ActivityCreate,
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, "Lead não encontrado")

    activity = Activity(lead_id=lead_id, **payload.model_dump())
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


@router.get("/{lead_id}/activities", response_model=list[ActivityOut])
def get_activities(lead_id: int, db: Session = Depends(get_db)):
    return (
        db.query(Activity)
        .filter(Activity.lead_id == lead_id)
        .order_by(Activity.created_at.desc())
        .all()
    )


def _lead_to_dict(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "company": lead.company,
        "status": lead.status.value,
        "source": lead.source.value,
        "value": lead.value,
        "campaign": lead.campaign,
    }
