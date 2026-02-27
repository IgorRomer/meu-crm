"""
CRM Backend — FastAPI + PostgreSQL
════════════════════════════════════

Endpoints:
  /leads          → CRUD leads, activities, stage moves
  /pipeline       → Pipeline stage management
  /webhooks       → Webhook CRUD + test + delivery history
  /webhooks/incoming/{slug} → Generic inbound webhook
  /meta/webhook   → Meta Lead Ads receiver (GET verify + POST receive)
  /meta/stats     → Meta integration stats
  /docs           → Swagger UI
  /redoc          → ReDoc
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import os

from database import engine
from models import Base
from routes import leads, webhooks, meta, pipeline


# ─── STARTUP / SHUTDOWN ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created/verified")

    # Seed default pipeline stages if empty
    from database import SessionLocal
    from models import PipelineStage
    db = SessionLocal()
    if db.query(PipelineStage).count() == 0:
        default_stages = [
            PipelineStage(name="Novo Lead",   color="#4f7cff", order=1),
            PipelineStage(name="Em Nutrição", color="#ffa502", order=2),
            PipelineStage(name="Qualificado", color="#a855f7", order=3),
            PipelineStage(name="Proposta",    color="#ff6b35", order=4),
            PipelineStage(name="Negociação",  color="#ffcc00", order=5),
            PipelineStage(name="Fechado",     color="#00e5a0", order=6),
        ]
        db.add_all(default_stages)
        db.commit()
        print("✅ Default pipeline stages seeded")
    db.close()

    # Start nurture background scheduler
    from services.nurture_scheduler import start_scheduler
    start_scheduler()
    print("✅ Nurture scheduler started")

    yield

    from services.nurture_scheduler import stop_scheduler
    stop_scheduler()
    print("🛑 CRM Backend shutting down")


# ─── APP ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="CRM Personalizado — API",
    description="""
## API do CRM Comercial

Backend completo para gestão de leads, pipeline de vendas, 
automação de nutrição e integração com Meta Lead Ads.

### Módulos
- **Leads** — CRUD completo, histórico de atividades, movimentação de etapas
- **Pipeline** — Gestão das etapas do funil de vendas
- **Webhooks** — Criação e gestão de webhooks de saída com assinatura HMAC
- **Meta Ads** — Receptor de leads do Facebook e Instagram Ads
    """,
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — adjust origins for production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Lock down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── ROUTERS ──────────────────────────────────────────────────────────────────

app.include_router(leads.router)
app.include_router(pipeline.router)
app.include_router(webhooks.router)
app.include_router(meta.router)


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

@app.get("/", tags=["frontend"])
def root():
    frontend = os.path.join(os.path.dirname(__file__), "frontend.html")
    if os.path.exists(frontend):
        return FileResponse(frontend, media_type="text/html")
    return {"status": "online", "service": "CRM Backend", "version": "1.0.0", "docs": "/docs"}

@app.get("/health", tags=["health"])
def health():
    from database import engine
    try:
        with engine.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
    return {"status": "ok", "database": db_status}
