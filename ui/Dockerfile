FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "streamlit>=1.30,<2" "httpx>=0.27,<1" "pydicom>=3.0,<4" "pynetdicom>=3.0,<4"

COPY app.py /app/app.py

ENV PYTHONUNBUFFERED=1
EXPOSE 8501

CMD ["streamlit", "run", "app.py", \
     "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
