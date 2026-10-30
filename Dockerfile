FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

LABEL org.opencontainers.image.title="PVP Reward Automation"
LABEL org.opencontainers.image.description="Daily Tencent PVP reward exchange with persistent Chromium login."

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PVP_DATA_DIR=/app/data
ENV PVP_HEADLESS=1
ENV TZ=Asia/Shanghai

WORKDIR /app

COPY --chown=pwuser:pwuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=pwuser:pwuser app ./app
COPY --chown=pwuser:pwuser README.md .

RUN mkdir -p /app/data /tmp/pvp-reward \
    && chown -R pwuser:pwuser /app /tmp/pvp-reward

USER pwuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

