from pydantic import BaseModel # type: ignore
from typing import Optional

class UserCreate(BaseModel):
    username: str
    email: str
    password: str


class Login(BaseModel):
    email: str
    password: str


class CustomerCreate(BaseModel):
    name: str
    email: str
    phone: str
    contact_person: str
    follow_up_date: str


class CustomerUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    contact_person: Optional[str] = None
    follow_up_date: Optional[str] = None


class InteractionCreate(BaseModel):
    talked_with: str
    interaction_date: Optional[str] = None
    remark: Optional[str] = ""