"""
Webhook Dispatcher Service
──────────────────────────
Responsible for:
  - Dispatching events to all active registered webhooks
  - HMAC-SHA256 payload signing
  - Retry logic with exponential backoff
  - Delivery logging to webhook_deliveries table
"""

import hmac
import hashlib
import json
import time
import asyncio
import httpx
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from models import Webhook, WebhookDelivery
import logging

logger = logging.getLogger("webhook_dispatcher")


def _sign_payload(secret: str, payload_bytes: bytes) -> str:
    """Generate HMAC-SHA256 signature for payload."""
    return "sha256=" + hmac.new(
        secret.encode(),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()


async def dispatch_event(
    db: Session,
    event: str,
    payload: Dict[str, Any],
    source_lead_id: Optional[int] = None
):
    """
    Find all active webhooks subscribed to `event` and dispatch asynchronously.
    Runs in background — does not block the API response.
    """
    webhooks = (
        db.query(Webhook)
        .filter(Webhook.is_active == True)
        .all()
    )

    subscribed = [w for w in webhooks if event in (w.events or [])]

    if not subscribed:
        return

    envelope = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": payload,
    }
    if source_lead_id:
        envelope["lead_id"] = source_lead_id

    # Fire all dispatches concurrently
    tasks = [_deliver(db, wh, event, envelope) for wh in subscribed]
    await asyncio.gather(*tasks, return_exceptions=True)


async def _deliver(
    db: Session,
    webhook: Webhook,
    event: str,
    envelope: Dict[str, Any],
    attempt: int = 1
):
    """Deliver a single webhook with retry on failure."""
    payload_bytes = json.dumps(envelope).encode()

    headers = {
        "Content-Type": "application/json",
        "X-CRM-Event": event,
        "X-CRM-Delivery": f"{webhook.id}-{int(time.time())}",
        **(webhook.headers or {}),
    }

    if webhook.secret:
        headers["X-CRM-Signature"] = _sign_payload(webhook.secret, payload_bytes)

    start = time.monotonic()
    response_status = None
    response_body   = None
    success         = False

    try:
        async with httpx.AsyncClient(timeout=webhook.timeout_sec) as client:
            resp = await client.request(
                method=webhook.method.value,
                url=webhook.url,
                content=payload_bytes,
                headers=headers,
            )
            response_status = resp.status_code
            response_body   = resp.text[:2000]
            success         = 200 <= resp.status_code < 300

    except httpx.TimeoutException:
        response_body = "Request timed out"
    except httpx.RequestError as e:
        response_body = str(e)

    duration_ms = int((time.monotonic() - start) * 1000)

    # Log delivery
    delivery = WebhookDelivery(
        webhook_id      = webhook.id,
        event           = event,
        payload         = envelope,
        response_status = response_status,
        response_body   = response_body,
        duration_ms     = duration_ms,
        success         = success,
        attempt         = attempt,
    )
    db.add(delivery)
    db.commit()

    # Retry on failure
    if not success and attempt < webhook.retry_count:
        backoff = 2 ** attempt  # 2s, 4s, 8s...
        logger.warning(
            f"Webhook {webhook.id} failed (attempt {attempt}). "
            f"Retrying in {backoff}s..."
        )
        await asyncio.sleep(backoff)
        await _deliver(db, webhook, event, envelope, attempt + 1)
    elif not success:
        logger.error(f"Webhook {webhook.id} permanently failed after {attempt} attempts.")


async def test_delivery(
    db: Session,
    webhook: Webhook,
    event: str,
    custom_payload: Optional[Dict[str, Any]] = None
) -> WebhookDelivery:
    """Send a test payload to the webhook URL and return the delivery record."""
    test_envelope = {
        "event": event,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test": True,
        "data": custom_payload or {
            "id": 0,
            "name": "Lead de Teste",
            "email": "teste@exemplo.com",
            "company": "Empresa Teste",
            "status": "Novo Lead",
            "source": "Manual",
        },
    }

    await _deliver(db, webhook, event, test_envelope)

    # Return the last delivery for this webhook
    return (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == webhook.id)
        .order_by(WebhookDelivery.id.desc())
        .first()
    )
