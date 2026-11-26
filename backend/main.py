import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import classifier
from auth import (
    hash_password,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_user_optional,
)
from database import engine, Base, get_db
from models import ScanHistory, WasteType, User, Rank, PointsLog

load_dotenv()
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Waste Classifier API")

# ---------- CORS ----------
# Mặc định cho phép mọi nơi gọi (đủ dùng khi phát triển).
# Khi deploy, đặt CORS_ORIGINS=https://app-cua-nguoi-3.com trong .env hoặc biến môi trường.
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,   # dùng token trong header, không dùng cookie
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Cấu hình ----------
MAX_SIZE = 5 * 1024 * 1024   # 85MB
CONF_THRESHOLD = 0.8         # dưới ngưỡng này thì trả "unknown"

# ---------- Cấu hình điểm ----------
POINTS_PER_CONFIRM = 10      # điểm cơ bản mỗi lần xác nhận xử lý
DAILY_POINT_CAP = 100        # tối đa điểm nhận được mỗi ngày
POINTS_MULTIPLIER = {        # hệ số theo loại rác (giá trị mẫu, nhóm tự chốt)
    "plastic": 1.0,
    "paper": 1.0,
    "cardboard": 1.0,
    "metal": 1.2,
    "glass": 1.5,
    "trash": 1.0,
    "organic": 1.0,
}
VN_TZ = timezone(timedelta(hours=7))


# ---------- Schemas ----------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=72)
    email: Optional[str] = Field(default=None, max_length=120)


class LoginIn(BaseModel):
    username: str
    password: str


class ConfirmIn(BaseModel):
    scan_id: int


# ---------- Helpers ----------
def detect_image_type(data: bytes):
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def waste_to_dict(wt: WasteType):
    return {
        "code": wt.code,
        "name": wt.name_vi,
        "group": wt.group_name,
        "bin_color": wt.guide.bin_color if wt.guide else None,
        "instructions": wt.guide.instructions if wt.guide else None,
    }


def load_ranks(db: Session):
    """Danh sách rank, sắp từ mốc cao xuống thấp."""
    return db.query(Rank).order_by(Rank.min_points.desc()).all()


def rank_for(ranks, points: int) -> Optional[str]:
    for r in ranks:  # đã sắp giảm dần: rank đầu tiên đạt mốc là rank hiện tại
        if points >= r.min_points:
            return r.name
    return None


def next_rank_info(ranks, points: int):
    higher = [r for r in ranks if r.min_points > points]
    if not higher:
        return None  # đã ở rank cao nhất
    nxt = min(higher, key=lambda r: r.min_points)
    return {"name": nxt.name, "points_needed": nxt.min_points - points}


def today_start_utc() -> datetime:
    """0h hôm nay theo giờ Việt Nam, đổi sang UTC (không tzinfo) để so với DB."""
    now_vn = datetime.now(VN_TZ)
    start_vn = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_vn.astimezone(timezone.utc).replace(tzinfo=None)


def points_earned_today(db: Session, user_id: int) -> int:
    total = (
        db.query(func.coalesce(func.sum(PointsLog.points), 0))
        .filter(PointsLog.user_id == user_id, PointsLog.created_at >= today_start_utc())
        .scalar()
    )
    return int(total)


# ---------- Hệ thống ----------
@app.get("/api/health")
def health():
    return {"status": "ok", "model_labels": classifier.LABELS}


# ---------- Tài khoản ----------
@app.post("/api/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if len(body.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Mật khẩu quá dài (tối đa 72 byte).")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Tên đăng nhập đã tồn tại.")
    if body.email and db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=409, detail="Email đã được sử dụng.")

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"success": True, "data": {"id": user.id, "username": user.username}}


@app.post("/api/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Sai tên đăng nhập hoặc mật khẩu.")
    return {
        "success": True,
        "data": {"access_token": create_access_token(user.id), "token_type": "bearer"},
    }


@app.get("/api/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ranks = load_ranks(db)
    total_scans = db.query(ScanHistory).filter(ScanHistory.user_id == user.id).count()
    confirmed = (
        db.query(ScanHistory)
        .filter(ScanHistory.user_id == user.id, ScanHistory.is_confirmed.is_(True))
        .count()
    )
    return {
        "success": True,
        "data": {
            "id": user.id,
            "username": user.username,
            "total_points": user.total_points,
            "rank": rank_for(ranks, user.total_points),
            "next_rank": next_rank_info(ranks, user.total_points),
            "points_today": points_earned_today(db, user.id),
            "daily_cap": DAILY_POINT_CAP,
            "total_scans": total_scans,
            "confirmed_scans": confirmed,
        },
    }


# ---------- Phân loại ----------
@app.post("/api/classify")
async def classify(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    data = await file.read()

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="File ảnh rỗng.")
    if len(data) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="Ảnh vượt quá giới hạn 5MB.")
    if detect_image_type(data) is None:
        raise HTTPException(status_code=415, detail="Chỉ hỗ trợ JPEG, PNG hoặc WEBP.")

    # Chạy model ở luồng riêng để không chặn các request khác
    try:
        result = await run_in_threadpool(classifier.predict, data)
    except ValueError:
        raise HTTPException(status_code=415, detail="Không đọc được ảnh (ảnh hỏng hoặc không hợp lệ).")

    label = result["label"]            # có thể là "unknown" nếu model thấy "None" (không có vật thể)
    if result["confidence"] < CONF_THRESHOLD:
        label = "unknown"

    waste = db.query(WasteType).filter(WasteType.code == label).first()

    record = ScanHistory(
        user_id=user.id if user else None,
        label=label,
        confidence=result["confidence"],
        waste_type_id=waste.id if waste else None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    return {
        "success": True,
        "data": {
            "scan_id": record.id,
            "predicted_class": label,
            "confidence": result["confidence"],
            "guide": waste_to_dict(waste) if waste else None,
        },
    }


# ---------- Xác nhận xử lý và cộng điểm ----------
@app.post("/api/confirm-disposal")
def confirm_disposal(
    body: ConfirmIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Khóa dòng này để hai request cùng lúc không cộng điểm 2 lần
    scan = (
        db.query(ScanHistory)
        .filter(ScanHistory.id == body.scan_id)
        .with_for_update()
        .first()
    )
    # Cùng một thông báo cho "không tồn tại" và "của người khác", để không lộ thông tin
    if scan is None or scan.user_id != user.id:
        raise HTTPException(status_code=404, detail="Không tìm thấy lần quét của bạn.")
    if scan.is_confirmed:
        raise HTTPException(status_code=409, detail="Lần quét này đã được xác nhận rồi.")
    if scan.label == "unknown" or scan.waste_type_id is None:
        raise HTTPException(
            status_code=400, detail="Không thể xác nhận lần quét chưa nhận diện được."
        )

    remaining = max(0, DAILY_POINT_CAP - points_earned_today(db, user.id))
    base = int(round(POINTS_PER_CONFIRM * POINTS_MULTIPLIER.get(scan.label, 1.0)))
    points = min(base, remaining)

    scan.is_confirmed = True
    if points > 0:
        db.add(
            PointsLog(
                user_id=user.id,
                scan_id=scan.id,
                points=points,
                reason=f"Xử lý đúng rác loại {scan.label}",
            )
        )
        db.query(User).filter(User.id == user.id).update(
            {User.total_points: User.total_points + points}
        )
    db.commit()
    db.refresh(user)

    ranks = load_ranks(db)
    return {
        "success": True,
        "data": {
            "points_earned": points,
            "total_points": user.total_points,
            "rank": rank_for(ranks, user.total_points),
            "next_rank": next_rank_info(ranks, user.total_points),
            "daily_cap_reached": points < base,
            "message": (
                f"+{points} điểm!"
                if points > 0
                else "Hôm nay bạn đã đạt giới hạn điểm, mai quay lại nhé."
            ),
        },
    }


# ---------- Bảng xếp hạng ----------
@app.get("/api/leaderboard")
def leaderboard(limit: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)):
    ranks = load_ranks(db)
    users = (
        db.query(User)
        .order_by(User.total_points.desc(), User.id.asc())
        .limit(limit)
        .all()
    )
    return {
        "success": True,
        "data": [
            {
                "position": i + 1,
                "username": u.username,
                "total_points": u.total_points,
                "rank": rank_for(ranks, u.total_points),
            }
            for i, u in enumerate(users)
        ],
    }


# ---------- Thư viện rác ----------
@app.get("/api/library")
def library(db: Session = Depends(get_db)):
    items = db.query(WasteType).all()
    return {"success": True, "data": [waste_to_dict(w) for w in items]}


@app.get("/api/library/{code}")
def library_item(code: str, db: Session = Depends(get_db)):
    wt = db.query(WasteType).filter(WasteType.code == code).first()
    if not wt:
        raise HTTPException(status_code=404, detail="Không tìm thấy loại rác.")
    return {"success": True, "data": waste_to_dict(wt)}


# ---------- Lịch sử cá nhân ----------
@app.get("/api/history")
def history(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ScanHistory)
        .filter(ScanHistory.user_id == user.id)
        .order_by(ScanHistory.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "success": True,
        "data": [
            {
                "id": r.id,
                "label": r.label,
                "confidence": r.confidence,
                "is_confirmed": r.is_confirmed,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }