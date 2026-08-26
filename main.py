import os
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# 1. CẤU HÌNH DATABASE CHUNG (SQLite)
# ==========================================
# Sử dụng chung 1 database để đồng bộ hoàn toàn giữa Game & Web
SQLALCHEMY_DATABASE_URL = "sqlite:///./app_data.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_utc_now():
    """Lấy thời gian UTC chuẩn không kèm tzinfo để tương thích với SQLite"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ==========================================
# 2. MODELS DATABASE
# ==========================================

# Bảng Device gộp đầy đủ các cột từ 2 file
class Device(Base):
    __tablename__ = "devices"

    device_id = Column(String, primary_key=True, index=True)
    ip_address = Column(String, index=True, nullable=True)
    is_activated = Column(Boolean, default=False)
    total_downloads = Column(Integer, default=0)
    updated_at = Column(DateTime, default=get_utc_now, onupdate=get_utc_now)

# Bảng Token kích hoạt dùng 1 lần
class OneTimeToken(Base):
    __tablename__ = "activation_tokens"

    token = Column(String, primary_key=True, index=True)
    device_id = Column(String, index=True)
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=get_utc_now)
    expires_at = Column(DateTime)

# Tự động tạo bảng nếu chưa có
Base.metadata.create_all(bind=engine)

# ==========================================
# 3. KHỞI TẠO FASTAPI & MIDDLEWARE
# ==========================================
app = FastAPI(title="Unified Game & Web Activation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_client_ip(request: Request) -> str:
    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"

def clean_id(raw_id: str) -> str:
    """Làm sạch và chuẩn hóa Device ID dạng DEV-XXXXX"""
    if not raw_id:
        return ""
    cleaned = str(raw_id).replace("DEV-", "").replace("dev-", "").strip()
    return f"DEV-{cleaned}" if cleaned else ""

# ==========================================
# 4. SCHEMAS (PYDANTIC)
# ==========================================
class TrackRequest(BaseModel):
    device_id: str

class StatusRequest(BaseModel):
    device_id: str

class ActivateRequest(BaseModel):
    device_id: str

class CreateTokenRequest(BaseModel):
    device_id: str

class VerifyTokenRequest(BaseModel):
    token: str

class DoActivateRequest(BaseModel):
    token: str
    device_id: str

# ==========================================
# 5. CÁC API ENDPOINTS
# ==========================================

# --- [API GAME CŨ] ---

@app.post("/api/track-download")
def track_download(req: TrackRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    dev_id = clean_id(req.device_id)
    
    if not dev_id:
        raise HTTPException(status_code=400, detail="ID không hợp lệ")

    device = db.query(Device).filter(Device.device_id == dev_id).first()
    
    if not device:
        device = Device(
            device_id=dev_id,
            ip_address=ip,
            total_downloads=1,
            is_activated=False
        )
        db.add(device)
    else:
        device.total_downloads += 1
        device.ip_address = ip
        
    db.commit()
    
    return {
        "status": "success", 
        "device_id": dev_id,
        "total_downloads": device.total_downloads
    }

@app.post("/api/check-status")
def check_status(req: StatusRequest, db: Session = Depends(get_db)):
    dev_id = clean_id(req.device_id)
    device = db.query(Device).filter(Device.device_id == dev_id).first()
    
    is_activated = False
    total_downloads = 0
    
    if device and device.is_activated:
        is_activated = True
        total_downloads = device.total_downloads
        
    return {
        "hide_ui": is_activated,
        "is_activated": is_activated,
        "activated": is_activated,
        "total_downloads": total_downloads
    }

@app.post("/api/activate")
def activate_device(req: ActivateRequest, db: Session = Depends(get_db)):
    dev_id = clean_id(req.device_id)

    if not dev_id:
        raise HTTPException(status_code=400, detail="Vui lòng nhập ID hợp lệ!")

    device = db.query(Device).filter(Device.device_id == dev_id).first()

    if not device:
        # Nếu chưa có trong DB thì tự động tạo mới và kích hoạt
        device = Device(device_id=dev_id, is_activated=True)
        db.add(device)
    else:
        device.is_activated = True

    db.commit()

    return {
        "status": "success",
        "message": f"Kích hoạt thành công thiết bị {dev_id}",
        "device_id": dev_id
    }

# --- [API WEB & TOKEN MỚI] ---

@app.post("/api/web/generate-link")
def generate_activation_link(req: CreateTokenRequest, db: Session = Depends(get_db)):
    dev_id = clean_id(req.device_id)
    if not dev_id:
        raise HTTPException(status_code=400, detail="ID thiết bị không hợp lệ")

    # Đảm bảo thiết bị đã tồn tại trong bảng Device
    device = db.query(Device).filter(Device.device_id == dev_id).first()
    if not device:
        device = Device(device_id=dev_id, is_activated=False)
        db.add(device)
        db.commit()

    raw_token = secrets.token_hex(16)
    now = get_utc_now()
    expire_time = now + timedelta(minutes=10)

    new_token = OneTimeToken(
        token=raw_token,
        device_id=dev_id,
        is_used=False,
        created_at=now,
        expires_at=expire_time
    )
    db.add(new_token)
    db.commit()

    # Link trang web kích hoạt
    web_url = f"https://guest-preview-363ecb.previewship.net?token={raw_token}&id={dev_id}"
    
    return {
        "status": "success",
        "token": raw_token,
        "activation_url": web_url,
        "expires_in_seconds": 600
    }

@app.post("/api/web/verify-token")
def verify_token(req: VerifyTokenRequest, db: Session = Depends(get_db)):
    token_item = db.query(OneTimeToken).filter(OneTimeToken.token == req.token).first()

    if not token_item:
        raise HTTPException(status_code=404, detail="Token không tồn tại hoặc link bị sai!")

    if token_item.is_used:
        raise HTTPException(status_code=410, detail="Link này ĐÃ ĐƯỢC SỬ DỤNG trước đó. Vui lòng lấy link mới từ Game!")

    if get_utc_now() > token_item.expires_at:
        raise HTTPException(status_code=408, detail="Link này ĐÃ HẾT HẠN (quá 10 phút). Vui lòng vào lại Game lấy link mới!")

    return {
        "status": "valid",
        "device_id": token_item.device_id,
        "message": "Token hợp lệ"
    }

@app.post("/api/web/activate")
def activate_device_via_web(req: DoActivateRequest, db: Session = Depends(get_db)):
    dev_id = clean_id(req.device_id)
    token_item = db.query(OneTimeToken).filter(OneTimeToken.token == req.token).first()

    if not token_item or token_item.is_used or get_utc_now() > token_item.expires_at:
        raise HTTPException(status_code=400, detail="Thao tác thất bại: Link đã bị vô hiệu hóa hoặc hết hạn!")

    if token_item.device_id != dev_id:
        raise HTTPException(status_code=400, detail="ID kích hoạt không khớp với Token!")

    # Đánh dấu Token đã sử dụng
    token_item.is_used = True

    # Cập nhật trạng thái Active cho Device
    device = db.query(Device).filter(Device.device_id == dev_id).first()
    if not device:
        device = Device(device_id=dev_id, is_activated=True)
        db.add(device)
    else:
        device.is_activated = True

    db.commit()

    return {
        "status": "success",
        "message": f"Kích hoạt thành công cho thiết bị {dev_id}!",
        "device_id": dev_id
    }

@app.get("/api/game/check-status/{device_id}")
def check_game_status(device_id: str, db: Session = Depends(get_db)):
    dev_id = clean_id(device_id)
    device = db.query(Device).filter(Device.device_id == dev_id).first()
    
    is_active = True if (device and device.is_activated) else False
    return {"is_activated": is_active, "device_id": dev_id}

# ==========================================
# 6. KHỞI CHẠY SERVER
# ==========================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
