from sqlalchemy import Column, Integer, String, Date
from database import Base

class Mahasiswa(Base):
    __tablename__ = "mahasiswa"

    id = Column(Integer, primary_key=True, index=True)
    nim = Column(String, unique=True, index=True, nullable=False)
    nama = Column(String, nullable=False)
    jurusan = Column(String, nullable=False)
    angkatan = Column(Integer, nullable=False)
    email = Column(String, unique=True, nullable=False)
    tanggal_lahir = Column(Date, nullable=True)