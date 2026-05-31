from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from uuid import UUID
from datetime import datetime


class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    wallet_address: Optional[str] = None


class UserCreate(UserBase):
    password: Optional[str] = None


class UserLogin(BaseModel):
    email: Optional[EmailStr] = None
    password: str


class UserResponse(UserBase):
    id: UUID
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyBase(BaseModel):
    name: str = Field(..., description="e.g. 'Telegram Bot', 'VS Code'")


class APIKeyCreate(APIKeyBase):
    pass


class APIKeyResponse(BaseModel):
    id: UUID
    name: str
    scopes: str
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class APIKeyCreateResponse(BaseModel):
    id: UUID
    name: str
    api_key: str = Field(..., description="Show ONLY ONCE. Store securely.")
    scopes: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until expiration")


class TokenData(BaseModel):
    user_id: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


# ── Phase 3 hardening (P3-A1, P3-A2, P3-A3, P3-A4) ───────────────────────────
class EmailVerificationConfirm(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)


class EmailVerificationResendRequest(BaseModel):
    """Request body for resending a verification email.

    The route is unauthenticated so a user who has lost the original
    email can request a new one. The response is the same whether or
    not the email is registered, to avoid user enumeration.
    """
    email: EmailStr


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)
    new_password: str = Field(..., min_length=8, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class SessionResponse(BaseModel):
    """One row from /auth/sessions.

    Returns the row id (so the client can revoke it via DELETE), the
    user_agent and ip_address recorded at issue time, and the
    timestamps. The token hash itself is never returned.
    """
    id: UUID
    issued_at: datetime
    expires_at: datetime
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None
    is_current: bool = False

    class Config:
        from_attributes = True