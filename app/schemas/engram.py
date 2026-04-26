from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class EngramBase(BaseModel):
    distilled_text: str
    source_type: Optional[str] = None


class EngramCreate(EngramBase):
    user_id: UUID
    actions: List[str] = []
    objects: List[str] = []
    entities: List[str] = []
    negated_actions: List[str] = []
    salience_scores: dict = {}


class EngramResponse(EngramBase):
    id: UUID
    user_id: UUID
    engram_id: str
    actions: List[str]
    objects: List[str]
    entities: List[str]
    negated_actions: List[str]
    salience_scores: dict
    connections: List[str]
    original_length: Optional[int]
    compressed_length: Optional[int]
    compression_ratio: Optional[float]
    created_at: datetime
    last_accessed_at: Optional[datetime]

    class Config:
        from_attributes = True


class EngramContextResponse(BaseModel):
    engram_id: str
    distilled_text: str
    entities: List[str]
    actions: List[str]
    connections: List[str]
    compression_ratio: Optional[float]
    token_count: int = Field(..., description="Tokens in distilled text")