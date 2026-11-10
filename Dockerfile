FROM python:3.12-slim

WORKDIR /opt/head-impact-platform

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PYTHONUNBUFFERED=1
ENV HEAD_IMPACT_DB=/data/head_impacts.duckdb
ENV HEAD_IMPACT_NAS_ROOT=/mnt/headimpact
ENV HEAD_IMPACT_EXPORT_DIR=/exports

EXPOSE 8501

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
