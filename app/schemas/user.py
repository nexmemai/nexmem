from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from uuid import UUID
from datetime import datetime


PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


def validate_password_complexity(password: Optional[str]) -> Optional[str]:
    if password is None:
        return password
    if not any(char.isupper() for char in password):
        raise ValueError("Password must contain at least one uppercase letter")
    if not any(char.islower() for char in password):
        raise ValueError("Password must contain at least one lowercase letter")
    if not any(char.isdigit() for char in password):
        raise ValueError("Password must contain at least one digit")
    return password


class UserBase(BaseModel):
    email: Optional[EmailStr] = None
    wallet_address: Optional[str] = None


class UserCreate(UserBase):
    password: Optional[str] = Field(
        None,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )

    @field_validator("password")
    @classmethod
    def password_complexity(cls, password: Optional[str]) -> Optional[str]:
        return validate_password_complexity(password)


class UserLogin(BaseModel):
    email: Optional[EmailStr] = None
    password: str = Field(
        ...,
        min_length=1,
        max_length=PASSWORD_MAX_LENGTH,
    )


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
