from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Rank(Base):
    __tablename__ = "ranks"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    min_points = Column(Integer, nullable=False)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(120), unique=True)
    password_hash = Column(String(255), nullable=False)
    total_points = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow)


class WasteType(Base):
    __tablename__ = "waste_types"
    id = Column(Integer, primary_key=True)
    code = Column(String(30), unique=True, nullable=False)   # vd: plastic
    name_vi = Column(String(100), nullable=False)            # vd: Nhựa
    group_name = Column(String(50))                          # vd: tai_che, huu_co
    description = Column(Text)
    guide = relationship("DisposalGuide", back_populates="waste_type", uselist=False)


class DisposalGuide(Base):
    __tablename__ = "disposal_guides"
    id = Column(Integer, primary_key=True)
    waste_type_id = Column(Integer, ForeignKey("waste_types.id"), unique=True, nullable=False)
    bin_color = Column(String(50))
    instructions = Column(Text, nullable=False)
    waste_type = relationship("WasteType", back_populates="guide")


class ScanHistory(Base):
    __tablename__ = "scan_history"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # tạm cho phép trống tới khi có đăng nhập
    waste_type_id = Column(Integer, ForeignKey("waste_types.id"), nullable=True)  # trống nếu unknown
    label = Column(String(30), nullable=False)
    confidence = Column(Float, nullable=False)
    is_confirmed = Column(Boolean, default=False, nullable=False)  # người dùng đã xác nhận xử lý chưa
    created_at = Column(DateTime, default=utcnow)


class PointsLog(Base):
    __tablename__ = "points_log"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    scan_id = Column(Integer, ForeignKey("scan_history.id"), nullable=True)
    points = Column(Integer, nullable=False)
    reason = Column(String(200))
    created_at = Column(DateTime, default=utcnow)
    