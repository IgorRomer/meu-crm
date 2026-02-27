from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import PipelineStage
from schemas import PipelineStageCreate, PipelineStageOut

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/stages", response_model=List[PipelineStageOut])
def list_stages(db: Session = Depends(get_db)):
    return db.query(PipelineStage).filter(PipelineStage.is_active == True).order_by(PipelineStage.order).all()


@router.post("/stages", response_model=PipelineStageOut, status_code=201)
def create_stage(payload: PipelineStageCreate, db: Session = Depends(get_db)):
    stage = PipelineStage(**payload.model_dump())
    db.add(stage)
    db.commit()
    db.refresh(stage)
    return stage


@router.patch("/stages/{stage_id}", response_model=PipelineStageOut)
def update_stage(stage_id: int, payload: PipelineStageCreate, db: Session = Depends(get_db)):
    stage = db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Etapa não encontrada")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(stage, k, v)
    db.commit()
    db.refresh(stage)
    return stage


@router.delete("/stages/{stage_id}", status_code=204)
def delete_stage(stage_id: int, db: Session = Depends(get_db)):
    stage = db.query(PipelineStage).filter(PipelineStage.id == stage_id).first()
    if not stage:
        raise HTTPException(404, "Etapa não encontrada")
    stage.is_active = False
    db.commit()
