# Dockerfile
# ใช้สำหรับ Build Python Application
FROM python:3.11-slim

# กำหนด working directory ภายใน container
WORKDIR /app

# คัดลอก requirements.txt เข้าไปเพื่อแยก layer install dependency
COPY requirements.txt .

# ติดตั้ง package ที่จำเป็นจาก requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# คัดลอก source code ทั้งหมดเข้าไปใน container
COPY . .

# (ถ้าต้องการ expose port เช่น 8000 สำหรับ FastAPI/Flask)
EXPOSE 3000

# สั่งให้รันแอปพลิเคชัน (ปรับตาม app ที่ใช้ เช่น main.py, app.py, ฯลฯ)
# ตัวอย่างสำหรับ FastAPI (uvicorn)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]