import os
import io
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import date
from minio import Minio
from minio.error import S3Error

import models
from database import engine, get_db

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_SECURE = os.getenv("MINIO_SECURE", "False").lower() == "true"

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE
)

models.Base.metadata.create_all(bind=engine)
app = FastAPI(title="Student File Management with MinIO")

class MahasiswaCreate(BaseModel):
    nim: str
    nama: str
    jurusan: str
    angkatan: int
    email: str
    tanggal_lahir: Optional[date] = None

class MahasiswaResponse(MahasiswaCreate):
    id: int
    class Config:
        from_attributes = True

class MahasiswaUpdate(BaseModel):
    nim: Optional[str] = None  
    nama: Optional[str] = None
    jurusan: Optional[str] = None
    angkatan: Optional[int] = None
    email: Optional[str] = None
    tanggal_lahir: Optional[date] = None


def get_student_bucket_name(nim: str) -> str:
    return f"student-{nim.lower()}"

def ensure_bucket_exists(bucket_name: str):
    if not minio_client.bucket_exists(bucket_name):
        minio_client.make_bucket(bucket_name)

def delete_bucket_force(bucket_name: str):
    if minio_client.bucket_exists(bucket_name):
        objects = minio_client.list_objects(bucket_name, recursive=True)
        for obj in objects:
            minio_client.remove_object(bucket_name, obj.object_name)
        minio_client.remove_bucket(bucket_name)

@app.post("/students", response_model=MahasiswaResponse, status_code=status.HTTP_201_CREATED)
def create_mahasiswa(mhs: MahasiswaCreate, db: Session = Depends(get_db)):
    existing_nim = db.query(models.Mahasiswa).filter(models.Mahasiswa.nim == mhs.nim).first()
    if existing_nim:
        raise HTTPException(400, "NIM already exists")
    existing_email = db.query(models.Mahasiswa).filter(models.Mahasiswa.email == mhs.email).first()
    if existing_email:
        raise HTTPException(400, "Email already exists")
    new_mhs = models.Mahasiswa(**mhs.dict())
    db.add(new_mhs)
    db.commit()
    db.refresh(new_mhs)
    
    # Buat bucket untuk mahasiswa ini berdasarkan NIM
    bucket = get_student_bucket_name(mhs.nim)
    ensure_bucket_exists(bucket)
    
    return new_mhs

@app.get("/students", response_model=List[MahasiswaResponse])
def list_mahasiswa(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(models.Mahasiswa).offset(skip).limit(limit).all()

@app.get("/students/{student_id}", response_model=MahasiswaResponse)
def get_mahasiswa(student_id: int, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    return mhs

@app.put("/students/{student_id}", response_model=MahasiswaResponse)
def update_mahasiswa(student_id: int, update_data: MahasiswaUpdate, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    
    if update_data.nim:
        raise HTTPException(400, "Cannot change NIM because bucket name depends on it")
    
    if update_data.email and update_data.email != mhs.email:
        if db.query(models.Mahasiswa).filter(models.Mahasiswa.email == update_data.email).first():
            raise HTTPException(400, "Email already used")
    for key, value in update_data.dict(exclude_unset=True).items():
        setattr(mhs, key, value)
    db.commit()
    db.refresh(mhs)
    return mhs

@app.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mahasiswa(student_id: int, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    
    bucket = get_student_bucket_name(mhs.nim)
    delete_bucket_force(bucket)
    
    db.delete(mhs)
    db.commit()
    return

@app.post("/students/{student_id}/upload")
async def upload_student_file(student_id: int, file: UploadFile = File(...), object_name: str = None, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    
    bucket = get_student_bucket_name(mhs.nim)
    ensure_bucket_exists(bucket)
    obj_name = object_name or file.filename
    content = await file.read()
    minio_client.put_object(bucket, obj_name, io.BytesIO(content), len(content), content_type=file.content_type)
    return {"message": f"File uploaded to student {mhs.nim} as '{obj_name}'"}

@app.get("/students/{student_id}/files")
def list_student_files(student_id: int, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    bucket = get_student_bucket_name(mhs.nim)
    if not minio_client.bucket_exists(bucket):
        return {"files": []}
    objects = minio_client.list_objects(bucket, recursive=True)
    result = [{"name": obj.object_name, "size": obj.size, "last_modified": obj.last_modified.isoformat()} for obj in objects]
    return {"files": result}

@app.delete("/students/{student_id}/files/{file_name:path}")
def delete_student_file(student_id: int, file_name: str, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    bucket = get_student_bucket_name(mhs.nim)
    try:
        minio_client.remove_object(bucket, file_name)
        return {"message": f"File '{file_name}' deleted"}
    except S3Error as e:
        raise HTTPException(404, f"File not found: {file_name}")

@app.put("/students/{student_id}/files/{file_name:path}/rename")
def rename_student_file(student_id: int, file_name: str, new_name: str, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    bucket = get_student_bucket_name(mhs.nim)
    try:
        minio_client.stat_object(bucket, file_name)  # check existence
        minio_client.copy_object(bucket, new_name, f"/{bucket}/{file_name}")
        minio_client.remove_object(bucket, file_name)
        return {"message": f"Renamed '{file_name}' to '{new_name}'"}
    except S3Error:
        raise HTTPException(404, f"File '{file_name}' not found")

@app.get("/students/{student_id}/files/{file_name:path}")
def download_student_file(student_id: int, file_name: str, db: Session = Depends(get_db)):
    mhs = db.query(models.Mahasiswa).filter(models.Mahasiswa.id == student_id).first()
    if not mhs:
        raise HTTPException(404, "Student not found")
    bucket = get_student_bucket_name(mhs.nim)
    try:
        response = minio_client.get_object(bucket, file_name)
        return StreamingResponse(response.stream(amt=1024*1024), media_type="application/octet-stream",
                                 headers={"Content-Disposition": f"attachment; filename={file_name}"})
    except S3Error:
        raise HTTPException(404, f"File '{file_name}' not found")

@app.get("/health")
def health():
    return {"status": "ok", "services": ["minio", "postgresql"]}