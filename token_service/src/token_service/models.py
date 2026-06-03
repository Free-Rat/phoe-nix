from datetime import datetime

from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    node_id: str = Field(min_length=1)


class TokenResponse(BaseModel):
    sas_url: str
    blob_path: str
    expires_at: datetime


class ErrorResponse(BaseModel):
    error: str
