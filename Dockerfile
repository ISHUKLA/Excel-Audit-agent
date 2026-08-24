FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The audit log and pipeline snapshots share this SQLite file. Keep it outside
# the image filesystem so evidence and sensitive workbook content live only in
# the explicitly mounted data volume.
ENV AUDIT_DB_PATH=/data/audit.db
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
