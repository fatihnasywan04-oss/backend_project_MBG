from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    
from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class EventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=5)
    location: str = Field(min_length=2, max_length=255)
    event_date: datetime
    price: Decimal = Field(ge=0)
    available_tickets: int = Field(ge=0)
    image_url: HttpUrl | None = None


class EventUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    description: str | None = Field(default=None, min_length=5)
    location: str | None = Field(default=None, min_length=2, max_length=255)
    event_date: datetime | None = None
    price: Decimal | None = Field(default=None, ge=0)
    available_tickets: int | None = Field(default=None, ge=0)
    image_url: HttpUrl | None = None


class EventResponse(BaseModel):
    id: int
    title: str
    description: str
    location: str
    event_date: datetime
    price: Decimal
    available_tickets: int
    image_url: str | None = None

    model_config = ConfigDict(from_attributes=True)