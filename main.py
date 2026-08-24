import os
from datetime import datetime
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Boolean, Integer, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# CẤU HÌNH DATABASE (SQLAlchemy)
# ==========================================
SQLALCHEMY_DATABASE_URL = "sqlite:///./devices.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in SQLALCHEMY_DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==========================================
# MODELS: BẢNG `devices`
# ==========================================
class Device(Base):
    __tablename__ = "devices"

    device_id = Column(String, primary_key=True, index=True)
    ip_address = Column(String, index=True)
    is_activated = Column(Boolean, default=False)
    total_downloads = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ==========================================
# KHỞI TẠO FASTAPI & CORS MIDDLEWARE
# ==========================================
app = FastAPI(title="Android WebView API")

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
    return request.client.host

# ==========================================
# PYDANTIC SCHEMAS
# ==========================================
class TrackRequest(BaseModel):
    device_id: str

class StatusRequest(BaseModel):
    device_id: str

# ==========================================
# CÁC API ENDPOINTS
# ==========================================

@app.post("/api/track-download")
def track_download(req: TrackRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    device = db.query(Device).filter(Device.device_id == req.device_id).first()
    
    if not device:
        device = Device(
            device_id=req.device_id,
            ip_address=ip,
            total_downloads=1
        )
        db.add(device)
    else:
        device.total_downloads += 1
        device.ip_address = ip
        
    db.commit()
    
    return {
        "status": "success", 
        "total_downloads": device.total_downloads
    }

@app.post("/api/check-status")
def check_status(req: StatusRequest, request: Request, db: Session = Depends(get_db)):
    ip = get_client_ip(request)
    device = db.query(Device).filter(Device.device_id == req.device_id).first()
    ip_activated = db.query(Device).filter(Device.ip_address == ip, Device.is_activated == True).first()
    
    is_activated = False
    total_downloads = 0
    
    if device:
        total_downloads = device.total_downloads
        if device.is_activated:
            is_activated = True
            
    if ip_activated:
        is_activated = True
        
    return {
        "hide_ui": is_activated,
        "is_activated": is_activated,
        "total_downloads": total_downloads
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
