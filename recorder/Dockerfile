# Recorder service — browser-driven SaaS explainer pipeline.
# Built on the official Playwright image (Node 20 + Chromium + system deps
# preinstalled, matching playwright 1.62.1), plus python3 + ffmpeg + OpenCV.
FROM mcr.microsoft.com/playwright:v1.62.1-noble

RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip ffmpeg curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/recorder

# node deps (browsers already in the base image)
COPY package.json package-lock.json ./
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN npm ci --omit=dev

# python deps (auto-zoom + speed-up)
COPY requirements.txt ./
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# polished-style Remotion renderer — deps bundled once (cached layer)
COPY remotion/package.json ./remotion/
RUN cd remotion && npm install --no-audit --no-fund --omit=dev

COPY . .

# install the caption/brand fonts system-wide so headless Chrome renders them.
# Remotion uses the Chromium already in the Playwright base image (see pipeline
# findChromium) — no Chrome download at build or render time.
RUN mkdir -p /usr/share/fonts/truetype/brand \
    && cp remotion/public/fonts/*.ttf /usr/share/fonts/truetype/brand/ 2>/dev/null || true \
    && cp fonts/*.ttf /usr/share/fonts/truetype/brand/ 2>/dev/null || true \
    && fc-cache -f

ENV FONTS_DIR=/srv/recorder/fonts \
    WORKDIR=/tmp/bc \
    REMOTION_DIR=/srv/recorder/remotion \
    NODE_OPTIONS=--max-old-space-size=2560

CMD ["node", "service.mjs"]
