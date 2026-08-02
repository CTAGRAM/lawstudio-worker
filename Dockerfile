FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-dejavu-core ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/app
COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./
ENV ASSETS_ROOT=/srv/app/assets \
    FONTS_DIR=/srv/app/assets \
    RUNS_DIR=/tmp/runs \
    CHECK_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
CMD ["python", "-u", "worker.py"]
