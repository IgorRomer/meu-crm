"""
Meta Lead Ads Webhook Routes
─────────────────────────────
GET  /meta/webhook  → Verification challenge (required by Meta)
POST /meta/webhook  → Receive lead events from Meta
GET  /meta/stats    → Integration statistics
"""

import hashlib
import hmac
import json
import os
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import Lead, Activity, MetaWebhookLog, LeadSource
from services.webhook_dispatcher import dispatch_event

router = APIRouter(prefix="/meta", tags=["meta"])

META_VERIFY_TOKEN  = os.getenv("META_VERIFY_TOKEN", "crm_meta_verify_token_2025")
META_APP_SECRET    = os.getenv("META_APP_SECRET", "")


# ─── VERIFICATION (GET) ───────────────────────────────────────────────────────

@router.get("/webhook")
def meta_verify(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    """
    Meta calls this endpoint to verify webhook ownership.
    Respond with hub.challenge when token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == META_VERIFY_TOKEN:
        return int(hub_challenge)
    raise HTTPException(403, "Token de verificação inválido")


# ─── RECEIVE LEADS (POST) ─────────────────────────────────────────────────────

@router.post("/webhook", status_code=200)
async def meta_receive(request: Request, db: Session = Depends(get_db)):
    """
    Receives lead events from Meta Lead Gen Ads.
    Validates HMAC-SHA256 signature when APP_SECRET is configured.
    """
    body_bytes = await request.body()

    # Validate signature
    if META_APP_SECRET:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected   = "sha256=" + hmac.new(
            META_APP_SECRET.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            raise HTTPException(403, "Assinatura inválida")

    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(400, "Payload inválido")

    leads_created = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue

            value   = change.get("value", {})
            form_id = value.get("form_id")
            ad_id   = value.get("ad_id")
            page_id = entry.get("id")

            # Log raw payload
            log = MetaWebhookLog(
                form_id=form_id,
                ad_id=ad_id,
                page_id=page_id,
                raw_payload=value,
            )
            db.add(log)
            db.flush()

            # Parse field data from Meta lead
            field_data = {
                f["name"]: f["values"][0]
                for f in value.get("field_data", [])
                if f.get("values")
            }

            name    = field_data.get("full_name") or field_data.get("name", "Lead Meta")
            email   = field_data.get("email")
            phone   = field_data.get("phone_number") or field_data.get("phone")
            company = field_data.get("company_name") or field_data.get("company")

            # Create lead
            lead = Lead(
                name=name,
                email=email,
                phone=phone,
                company=company,
                source=LeadSource.facebook,
                ad_id=ad_id,
                form_id=form_id,
                campaign=value.get("ad_name") or ad_id,
                custom_fields=field_data,
            )
            db.add(lead)
            db.flush()

            log.lead_id   = lead.id
            log.processed = True

            db.add(Activity(
                lead_id=lead.id,
                type="meta_import",
                description=f"Lead importado do Facebook Lead Ads (form: {form_id})",
                metadata={"form_id": form_id, "ad_id": ad_id, "page_id": page_id},
            ))

            leads_created.append(lead)

    db.commit()

    # Dispatch webhook events for each new lead
    for lead in leads_created:
        db.refresh(lead)
        await dispatch_event(
            db, "meta.lead_received",
            {
                "id": lead.id,
                "name": lead.name,
                "email": lead.email,
                "phone": lead.phone,
                "company": lead.company,
                "ad_id": lead.ad_id,
                "form_id": lead.form_id,
                "campaign": lead.campaign,
            },
            lead.id,
        )

    return {"status": "ok", "leads_created": len(leads_created)}


# ─── STATS ────────────────────────────────────────────────────────────────────

@router.get("/stats")
def meta_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    from models import Lead

    total_fb = db.query(Lead).filter(Lead.source == LeadSource.facebook).count()
    total_ig = db.query(Lead).filter(Lead.source == LeadSource.instagram if hasattr(LeadSource, 'instagram') else Lead.source == LeadSource.facebook).count()
    total_logs = db.query(MetaWebhookLog).count()
    processed  = db.query(MetaWebhookLog).filter(MetaWebhookLog.processed == True).count()

    return {
        "facebook_leads": total_fb,
        "webhook_logs": total_logs,
        "processed_logs": processed,
        "verify_token_configured": bool(META_VERIFY_TOKEN),
        "app_secret_configured": bool(META_APP_SECRET),
    }
