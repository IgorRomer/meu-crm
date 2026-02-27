"""
Nurture Scheduler — APScheduler (sem Redis, sem Celery)
───────────────────────────────────────────────────────
Roda dentro do mesmo processo FastAPI.
A cada minuto verifica enrollments pendentes e executa o próximo passo.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
import logging

from database import SessionLocal
from models import NurtureEnrollment, NurtureStep, Activity, NurtureStepType
from services.webhook_dispatcher import dispatch_event

logger = logging.getLogger("nurture_scheduler")
scheduler = AsyncIOScheduler()


async def process_nurture_queue():
    """Check and execute pending nurture steps."""
    db: Session = SessionLocal()
    try:
        now = datetime.now(timezone.utc)

        pending = (
            db.query(NurtureEnrollment)
            .filter(
                NurtureEnrollment.status == "active",
                NurtureEnrollment.next_step_at <= now,
            )
            .limit(50)
            .all()
        )

        for enrollment in pending:
            await _execute_step(db, enrollment)

        if pending:
            logger.info(f"Processed {len(pending)} nurture steps")

    except Exception as e:
        logger.error(f"Nurture scheduler error: {e}")
    finally:
        db.close()


async def _execute_step(db: Session, enrollment: NurtureEnrollment):
    sequence = enrollment.sequence
    steps = sorted(sequence.steps, key=lambda s: s.order)

    if enrollment.current_step >= len(steps):
        enrollment.status = "completed"
        db.commit()
        return

    step: NurtureStep = steps[enrollment.current_step]

    # Execute step based on type
    if step.type == NurtureStepType.email:
        await _send_email(db, enrollment, step)
    elif step.type == NurtureStepType.whatsapp:
        await _send_whatsapp(db, enrollment, step)
    elif step.type == NurtureStepType.wait:
        pass  # Just advance timing

    # Log activity
    db.add(Activity(
        lead_id=enrollment.lead_id,
        type=f"nurture_{step.type.value}",
        description=f"[{sequence.name}] Passo {enrollment.current_step + 1}: {step.subject or step.type.value}",
        metadata={"sequence_id": sequence.id, "step_order": step.order},
    ))

    # Advance to next step
    enrollment.current_step += 1
    if enrollment.current_step < len(steps):
        next_step = steps[enrollment.current_step]
        enrollment.next_step_at = datetime.now(timezone.utc) + timedelta(
            hours=next_step.delay_hours or 0
        )
    else:
        enrollment.status = "completed"

    db.commit()

    await dispatch_event(
        db, "nurture.sent",
        {
            "lead_id": enrollment.lead_id,
            "sequence": sequence.name,
            "step_type": step.type.value,
            "step_order": step.order,
        },
        enrollment.lead_id,
    )


async def _send_email(db: Session, enrollment: NurtureEnrollment, step: NurtureStep):
    """
    Hook para integrar com Brevo / SendGrid / etc.
    Substitua pelo SDK do seu provedor de email.
    """
    lead = enrollment.lead
    logger.info(
        f"EMAIL → {lead.email} | Subject: {step.subject} | Lead: {lead.name}"
    )
    # Example Brevo integration:
    # import sib_api_v3_sdk
    # ...


async def _send_whatsapp(db: Session, enrollment: NurtureEnrollment, step: NurtureStep):
    """
    Hook para integrar com Meta Cloud API (WhatsApp Business).
    """
    lead = enrollment.lead
    logger.info(
        f"WHATSAPP → {lead.phone} | Lead: {lead.name}"
    )
    # Example Meta WhatsApp Cloud API:
    # import httpx
    # await httpx.post("https://graph.facebook.com/v18.0/.../messages", ...)


def start_scheduler():
    scheduler.add_job(
        process_nurture_queue,
        trigger=IntervalTrigger(minutes=1),
        id="nurture_queue",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("✅ Nurture scheduler started (every 60s)")


def stop_scheduler():
    scheduler.shutdown()
