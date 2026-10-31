FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

LABEL org.opencontainers.image.title="PVP Reward Automation"
LABEL org.opencontainers.image.description="Daily Tencent PVP reward exchange with persistent Chromium login."

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PVP_DATA_DIR=/app/data
ENV DISPLAY=:99
ENV PVP_HEADLESS=0
ENV PVP_REMOTE_DESKTOP=1
ENV PVP_NOVNC_PORT=6080
ENV TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        novnc \
        openbox \
        websockify \
        x11-utils \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=pwuser:pwuser requirements.txt .
RUN pip install --no-cache-dir --ignore-installed typing_extensions \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=pwuser:pwuser app ./app
COPY --chown=pwuser:pwuser README.md .
COPY --chown=pwuser:pwuser scripts/docker-entrypoint.sh /usr/local/bin/pvp-entrypoint

RUN mkdir -p /app/data /tmp/pvp-reward \
    && chown -R pwuser:pwuser /app /tmp/pvp-reward \
    && chmod 755 /usr/local/bin/pvp-entrypoint

USER pwuser

EXPOSE 8000 6080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3); urllib.request.urlopen('http://127.0.0.1:6080/vnc.html', timeout=3)"

ENTRYPOINT ["/usr/local/bin/pvp-entrypoint"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

