FROM python:3.11-slim

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /app

# requirements 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 코드 복사
COPY . .

# 로그/데이터 디렉토리 생성
RUN mkdir -p logs data/captures

EXPOSE 8080

CMD ["python", "-m", "streamlit", "run", "src/dashboard/app.py", "--server.port=8080", "--server.address=0.0.0.0"]