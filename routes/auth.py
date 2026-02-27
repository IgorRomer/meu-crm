"""
Auth Routes
────────────
POST /auth/login          → Login, retorna JWT
POST /auth/register       → Criar conta (admin only após primeiro usuário)
GET  /auth/me             → Dados do usuário logado
GET  /auth/users          → Listar usuários (admin only)
PATCH /auth/users/{id}    → Editar usuário (admin only)
DELETE /auth/users/{id}   → Desativar usuário (admin only)
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel, EmailStr
from typing import Optional

from database import get_db
from models import User
from services.auth import (
    hash_password, verify_password, create_token,
    get_current_user, require_admin
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ─── SCHEMAS ──────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    name: str
    email: str
    password: str
    role: str = "vendedor"

class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    class Config: from_attributes = True

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ─── LOGIN ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenOut)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == form.username, User.is_active == True).first()
    if not user or not verify_password(form.password, user.password):
        raise HTTPException(401, "Email ou senha incorretos")

    token = create_token({"user_id": user.id, "role": user.role, "name": user.name})
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


# ─── ME ───────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


# ─── REGISTER ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """
    Primeiro usuário pode se registrar livremente (bootstrap).
    Após isso, apenas admins podem criar usuários via /auth/users.
    """
    total = db.query(User).count()
    if total > 0:
        raise HTTPException(403, "Registro público desabilitado. Peça ao admin para criar sua conta.")

    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email já cadastrado")

    user = User(
        name=payload.name,
        email=payload.email,
        password=hash_password(payload.password),
        role="admin",   # primeiro usuário vira admin automaticamente
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ─── LIST USERS (admin) ───────────────────────────────────────────────────────

@router.get("/users", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    return db.query(User).order_by(User.created_at).all()


# ─── CREATE USER (admin) ──────────────────────────────────────────────────────

@router.post("/users", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _=Depends(require_admin)
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email já cadastrado")

    user = User(
        name=payload.name,
        email=payload.email,
        password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ─── UPDATE USER (admin) ──────────────────────────────────────────────────────

@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuário não encontrado")

    updates = payload.model_dump(exclude_unset=True)
    if "password" in updates:
        updates["password"] = hash_password(updates["password"])
    for k, v in updates.items():
        setattr(user, k, v)

    db.commit()
    db.refresh(user)
    return user


# ─── DELETE USER (admin) ──────────────────────────────────────────────────────

@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    if user_id == current_user.id:
        raise HTTPException(400, "Você não pode desativar sua própria conta")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "Usuário não encontrado")
    user.is_active = False
    db.commit()
