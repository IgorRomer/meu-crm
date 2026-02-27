"""
Webhook Management Routes
─────────────────────────
POST   /webhooks/                  → Create webhook
GET    /webhooks/                  → List all webhooks
GET    /webhooks/{id}              → Get webhook details
PATCH  /webhooks/{id}              → Update webhook
DELETE /webhooks/{id}              → Delete webhook
POST   /webhooks/{id}/test         → Send test payload
GET    /webhooks/{id}/deliveries   → Delivery history
GET    /webhooks/events            → List available events
POST   /webhooks/incoming/{slug}   → Receive external webhook (generic inbound)
"""

import secrets
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Webhook, WebhookDelivery, WebhookEvent
from schemas import (
    WebhookCreate, WebhookUpdate, WebhookOut,
    WebhookDeliveryOut, WebhookTestRequest
)
from services.webhook_dispatcher import test_delivery

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ─── LIST AVAILABLE EVENTS ────────────────────────────────────────────────────

@router.get("/events")
def list_events():
    """Returns all event types the CRM can emit."""
    return {
        "events": [
            {
                "value": e.value,
                "label": {
                    "lead.created":       "Lead Criado",
                    "lead.updated":       "Lead Atualizado",
                    "lead.stage_changed": "Lead Mudou de Etapa",
                    "lead.closed":        "Lead Fechado (Venda)",
                    "nurture.sent":       "Nutrição Enviada",
                    "meta.lead_received": "Lead Recebido do Meta",
                }.get(e.value, e.value),
                "description": {
                    "lead.created":       "Disparado toda vez que um novo lead é criado no CRM.",
                    "lead.updated":       "Disparado quando qualquer campo de um lead é alterado.",
                    "lead.stage_changed": "Disparado quando um lead muda de etapa no pipeline.",
                    "lead.closed":        "Disparado quando um lead é marcado como 'Fechado'.",
                    "nurture.sent":       "Disparado quando uma mensagem de nutrição é enviada.",
                    "meta.lead_received": "Disparado quando um lead chega via Meta Lead Ads.",
                }.get(e.value, ""),
            }
            for e in WebhookEvent
        ]
    }


# ─── CREATE ───────────────────────────────────────────────────────────────────

@router.post("/", response_model=WebhookOut, status_code=201)
def create_webhook(payload: WebhookCreate, db: Session = Depends(get_db)):
    # Auto-generate signing secret if not provided
    secret = payload.secret or secrets.token_hex(32)
    wh = Webhook(
        name=payload.name,
        description=payload.description,
        url=str(payload.url),
        method=payload.method,
        secret=secret,
        events=payload.events,
        headers=payload.headers,
        retry_count=payload.retry_count,
        timeout_sec=payload.timeout_sec,
    )
    db.add(wh)
    db.commit()
    db.refresh(wh)
    return wh


# ─── LIST ─────────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[WebhookOut])
def list_webhooks(
    db: Session = Depends(get_db),
    is_active: Optional[bool] = None,
):
    q = db.query(Webhook)
    if is_active is not None:
        q = q.filter(Webhook.is_active == is_active)
    return q.order_by(Webhook.created_at.desc()).all()


# ─── GET ──────────────────────────────────────────────────────────────────────

@router.get("/{wh_id}", response_model=WebhookOut)
def get_webhook(wh_id: int, db: Session = Depends(get_db)):
    wh = db.query(Webhook).filter(Webhook.id == wh_id).first()
    if not wh:
        raise HTTPException(404, "Webhook não encontrado")
    return wh


# ─── UPDATE ───────────────────────────────────────────────────────────────────

@router.patch("/{wh_id}", response_model=WebhookOut)
def update_webhook(
    wh_id: int,
    payload: WebhookUpdate,
    db: Session = Depends(get_db),
):
    wh = db.query(Webhook).filter(Webhook.id == wh_id).first()
    if not wh:
        raise HTTPException(404, "Webhook não encontrado")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(wh, k, v)

    db.commit()
    db.refresh(wh)
    return wh


# ─── DELETE ───────────────────────────────────────────────────────────────────

@router.delete("/{wh_id}", status_code=204)
def delete_webhook(wh_id: int, db: Session = Depends(get_db)):
    wh = db.query(Webhook).filter(Webhook.id == wh_id).first()
    if not wh:
        raise HTTPException(404, "Webhook não encontrado")
    db.delete(wh)
    db.commit()


# ─── TOGGLE ACTIVE ────────────────────────────────────────────────────────────

@router.post("/{wh_id}/toggle", response_model=WebhookOut)
def toggle_webhook(wh_id: int, db: Session = Depends(get_db)):
    wh = db.query(Webhook).filter(Webhook.id == wh_id).first()
    if not wh:
        raise HTTPException(404, "Webhook não encontrado")
    wh.is_active = not wh.is_active
    db.commit()
    db.refresh(wh)
    return wh


# ─── TEST DELIVERY ────────────────────────────────────────────────────────────

@router.post("/{wh_id}/test", response_model=WebhookDeliveryOut)
async def test_webhook(
    wh_id: int,
    payload: WebhookTestRequest,
    db: Session = Depends(get_db),
):
    wh = db.query(Webhook).filter(Webhook.id == wh_id).first()
    if not wh:
        raise HTTPException(404, "Webhook não encontrado")

    delivery = await test_delivery(db, wh, payload.event, payload.payload)
    if not delivery:
        raise HTTPException(500, "Erro ao executar teste")
    return delivery


# ─── DELIVERY HISTORY ─────────────────────────────────────────────────────────

@router.get("/{wh_id}/deliveries", response_model=List[WebhookDeliveryOut])
def get_deliveries(
    wh_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, le=200),
    success_only: Optional[bool] = None,
):
    q = db.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wh_id)
    if success_only is not None:
        q = q.filter(WebhookDelivery.success == success_only)
    return q.order_by(WebhookDelivery.created_at.desc()).limit(limit).all()


# ─── INBOUND GENERIC WEBHOOK ─────────────────────────────────────────────────
# Other systems can POST data to /webhooks/incoming/{slug} to create leads

@router.post("/incoming/{slug}", status_code=200)
async def receive_inbound_webhook(
    slug: str,
    request_body: dict,
    db: Session = Depends(get_db),
):
    """
    Generic inbound webhook endpoint.
    Third-party systems can POST lead data here.
    Expected body: { name, email, phone, company, source, campaign, ... }
    The `slug` can be used to identify the source/integration.
    """
    from models import Lead, Activity, LeadSource

    name = request_body.get("name") or request_body.get("full_name", "Sem nome")
    lead = Lead(
        name=name,
        email=request_body.get("email"),
        phone=request_body.get("phone") or request_body.get("phone_number"),
        company=request_body.get("company") or request_body.get("company_name"),
        job_title=request_body.get("job_title"),
        source=LeadSource.webhook,
        campaign=request_body.get("campaign") or slug,
        custom_fields=request_body,
    )
    db.add(lead)
    db.flush()

    db.add(Activity(
        lead_id=lead.id,
        type="webhook_inbound",
        description=f"Lead recebido via webhook inbound (slug: {slug})",
        metadata={"slug": slug, "raw": request_body},
    ))
    db.commit()

    return {"status": "ok", "lead_id": lead.id, "message": "Lead criado com sucesso"}
