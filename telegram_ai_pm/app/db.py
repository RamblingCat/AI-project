import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, BigInteger, String, Text, DateTime, ForeignKey, Boolean, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

class Base(DeclarativeBase): pass
engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
Session = sessionmaker(bind=engine, expire_on_commit=False)

def utcnow(): return datetime.now(timezone.utc)

class Project(Base):
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True)
    title = Column(String(250), nullable=False)
    goal = Column(Text, default='')
    success_criteria = Column(Text, default='')
    status = Column(String(30), default='draft', nullable=False)
    scope = Column(String(20), default='work')
    created_at = Column(DateTime(timezone=True), default=utcnow)

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, default='')
    criteria = Column(Text, default='')
    status = Column(String(30), default='planned')
    priority = Column(String(2), default='P2')
    due_at = Column(DateTime(timezone=True), nullable=True)
    estimate_minutes = Column(Integer, nullable=True)
    effort_breakdown = Column(Text, nullable=True)
    reschedules = Column(Integer, default=0)
    last_reminded_at = Column(DateTime(timezone=True), nullable=True)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class Evidence(Base):
    __tablename__ = 'evidence'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    content = Column(Text, nullable=False)
    verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class EventLog(Base):
    __tablename__ = 'event_log'
    id = Column(Integer, primary_key=True)
    kind = Column(String(60), nullable=False)
    object_id = Column(Integer, nullable=True)
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class Notification(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)
    key = Column(String(150), unique=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class Message(Base):
    __tablename__ = 'messages'
    id = Column(Integer, primary_key=True)
    role = Column(String(15), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String(80), primary_key=True)
    value = Column(Text, nullable=False)
