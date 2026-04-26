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
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until expiration")


class TokenData(BaseModel):
    user_id: Optional[str] = None