from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text
from datetime import datetime
if __package__:
    from .database import Base
else:
    from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, index=True)
    email = Column(String(255), unique=True, index=True)
    password = Column(String(255))
    is_admin = Column(Boolean, default=False)  # Add this
    created_at = Column(DateTime, default=datetime.utcnow)

class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    name = Column(String(255))
    email = Column(String(255))
    phone = Column(String(50))
    contact_person = Column(String(255))
    follow_up_date = Column(String(50))


class CustomerInteraction(Base):
    """Call / talk history and remarks per customer."""

    __tablename__ = "customer_interactions"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, nullable=False, index=True)
    talked_with = Column(String(255))
    interaction_date = Column(String(50))
    remark = Column(Text)
    created_at = Column(String(50))