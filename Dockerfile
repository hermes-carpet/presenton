# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-trixie AS fastapi-builder

WORKDIR /app/servers/fastapi

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN python -m venv --without-pip /opt/venv \
    && pip install --no-cache-dir uv

COPY servers/fastapi/pyproject.toml servers/fastapi/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && uv pip install --python /opt/venv/bin/python -r /tmp/requirements.txt

# mem0/spaCy BM25 lemmatization loads en_core_web_sm at runtime; spaCy tries pip to
# download it otherwise. Runtime image has no pip in PATH (--without-pip venv).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python \
    "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# The backend project is not installed into the venv. A constant .pth file puts
# /app/servers/fastapi on sys.path instead (same import order as an installed
# package), so /opt/venv changes only with uv.lock and code-only updates don't
# rewrite the venv layer.
RUN echo /app/servers/fastapi > "$(/opt/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/presenton-backend.pth"

COPY servers/fastapi /app/servers/fastapi
ENV HF_HOME=/root/.cache/huggingface \
    PRESENTON_FASTEMBED_ICON_CACHE_DIR=/root/.cache/presenton/fastembed-icons
# Warm FastEmbed caches into the image (not a BuildKit cache mount, or HF weights would be missing).
RUN /opt/venv/bin/python scripts/warm_fastembed_cache.py


FROM node:22-bookworm-slim AS nextjs-builder

WORKDIR /app/servers/nextjs

ENV NEXT_TELEMETRY_DISABLED=1

COPY servers/nextjs/package.json servers/nextjs/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY servers/nextjs /app/servers/nextjs
RUN npm run build \
    && rm -rf .next-build/cache


FROM node:22-bookworm-slim AS assets-builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates unzip \
    && rm -rf /var/lib/apt/lists/*

COPY package.json /app/

RUN mkdir -p /app/document-extraction-liteparse \
    && cd /app/document-extraction-liteparse \
    && npm init -y \
    && npm install @llamaindex/liteparse@1.5.2 --omit=dev

COPY electron/resources/document-extraction/liteparse_runner.mjs /app/document-extraction-liteparse/liteparse_runner.mjs
COPY scripts/sync-presentation-export.cjs /app/scripts/sync-presentation-export.cjs
COPY scripts/run-presentation-export.mjs /app/scripts/run-presentation-export.mjs
RUN rm -rf /app/presentation-export \
    && node /app/scripts/sync-presentation-export.cjs --force

# Fork: chrome-headless-shell at exactly the version the export runtime's
# puppeteer pins. Chrome for Testing publishes Linux x64 only; arm64 images use
# Debian's chromium-headless-shell instead (see the runtime stage).
ARG TARGETARCH
RUN mkdir -p /opt/chrome-headless-shell \
    && if [ "$TARGETARCH" = "amd64" ]; then \
    cd /app/presentation-export \
    && node --input-type=module -e ' \
    import { install } from "@puppeteer/browsers"; \
    import { PUPPETEER_REVISIONS } from "puppeteer-core/internal/revisions.js"; \
    await install({ browser: "chrome-headless-shell", buildId: PUPPETEER_REVISIONS["chrome-headless-shell"], cacheDir: "/opt/chrome-headless-shell" });' \
    && ln -s "$(find /opt/chrome-headless-shell -type f -name chrome-headless-shell)" /opt/chrome-headless-shell/headless-shell; \
    fi


FROM python:3.11-slim-trixie AS runtime

WORKDIR /app

ARG INSTALL_TESSERACT=true
ARG TARGETARCH
ARG CHROMIUM_VERSION=149.0.7827.196-1~deb13u1
ARG CHROMIUM_SNAPSHOT=20260625T180000Z

# LiteParse uses Node + @llamaindex/liteparse (same runner as Electron); OCR uses Tesseract.
ENV APP_DATA_DIRECTORY=/app_data \
    TEMP_DIRECTORY=/tmp/presenton \
    EXPORT_PACKAGE_ROOT=/app/presentation-export \
    PRESENTON_APP_ROOT=/app \
    HF_HOME=/root/.cache/huggingface \
    PRESENTON_FASTEMBED_ICON_CACHE_DIR=/root/.cache/presenton/fastembed-icons \
    PATH="/opt/venv/bin:${PATH}" \
    NODE_ENV=production \
    START_OLLAMA=false \
    PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chrome-headless-shell

RUN set -eux; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s trixie-security main\n' "$CHROMIUM_SNAPSHOT" > /etc/apt/sources.list.d/chromium-snapshot.list; \
    # Fork: headless-only Chrome for exports (PDF/PPTX, template previews) and no
    # desktop/GUI stack. Fonts: Latin and symbols plus emoji as fallbacks; slides
    # use their own web fonts.
    packages="ca-certificates curl nginx fontconfig imagemagick zstd \
    fonts-noto-core fonts-noto-color-emoji"; \
    if [ "$INSTALL_TESSERACT" = "true" ]; then packages="$packages tesseract-ocr tesseract-ocr-eng"; fi; \
    apt-get update; \
    if [ "$TARGETARCH" = "amd64" ]; then \
    # Libraries chrome-headless-shell links (from ldd).
    apt-get install -y --no-install-recommends $packages \
    libasound2t64 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libdbus-1-3 \
    libnss3 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxrandr2 \
    libdrm2 libwayland-server0; \
    # libgbm1 hard-depends on mesa's Gallium/LLVM stack (~190 MB), which headless
    # rendering never loads (libgbm.so.1 links only libdrm/libexpat; GPU backends
    # are dlopened). Install it repackaged without that dependency, or fall back
    # to the stock package.
    if (cd /tmp && apt-get download libgbm1 && dpkg-deb -R libgbm1_*.deb gbm \
    && sed -i -E 's/, mesa-libgallium \([^)]*\)//; s/^(Version: .*)/\1+fork1/' gbm/DEBIAN/control \
    && dpkg-deb --build gbm gbm.deb && dpkg -i gbm.deb); then \
    apt-mark hold libgbm1; \
    else \
    echo "warning: installing stock libgbm1 (pulls mesa)"; \
    apt-get install -y --no-install-recommends libgbm1; \
    fi; \
    rm -rf /tmp/gbm /tmp/gbm.deb /tmp/libgbm1_*.deb; \
    ln -s /opt/chrome-headless-shell/headless-shell /usr/local/bin/chrome-headless-shell; \
    else \
    apt-get install -y --no-install-recommends --allow-downgrades $packages \
    chromium-headless-shell="${CHROMIUM_VERSION}" \
    chromium-common="${CHROMIUM_VERSION}"; \
    apt-mark hold chromium-headless-shell chromium-common; \
    ln -s /usr/lib/chromium/chromium-headless-shell /usr/local/bin/chrome-headless-shell; \
    fi; \
    # Upstream's docker-compose.yml sets PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium.
    ln -s /usr/local/bin/chrome-headless-shell /usr/bin/chromium; \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*

# Remove any non-Noto fonts that may have been installed as dependencies.
RUN find /usr/share/fonts -type f ! -iname 'Noto*' -delete \
    && find /usr/share/fonts -type d -empty -delete \
    && fc-cache -fsv

RUN mkdir -p /app/scripts /app/servers/fastapi /app/servers/nextjs
RUN mkdir -p /app_data/exports /app_data/images /app_data/uploads /app_data/fonts /app_data/templates /app_data/pptx-to-html /app_data/pptx-to-json \
    && chmod -R a+rX /app_data

# Runtime copies use --link so each layer is reused whenever its own content is
# unchanged, even if earlier layers changed (e.g. a Chromium bump or a backend
# code update); pulls then only fetch what actually changed.
COPY --link --from=fastapi-builder /opt/venv /opt/venv
COPY --link --from=fastapi-builder /root/.cache/huggingface /root/.cache/huggingface
COPY --link --from=fastapi-builder /root/.cache/presenton/fastembed-icons /root/.cache/presenton/fastembed-icons
COPY --link templates /app/templates

COPY --link --from=assets-builder /app/package.json /app/package.json
COPY --link --from=assets-builder /app/document-extraction-liteparse /app/document-extraction-liteparse
COPY --link --from=assets-builder /app/presentation-export /app/presentation-export
COPY --link --from=assets-builder /app/scripts/sync-presentation-export.cjs /app/scripts/sync-presentation-export.cjs

COPY --link --from=assets-builder /opt/chrome-headless-shell /opt/chrome-headless-shell

RUN test -f /app/presentation-export/runner.mjs \
    && test -f /app/presentation-export/node_modules/@presenton/export-core/dist/index.js \
    && ! ldd "$(readlink -f "$PUPPETEER_EXECUTABLE_PATH")" | grep "not found" \
    && "$PUPPETEER_EXECUTABLE_PATH" --version

COPY --link --from=nextjs-builder /app/servers/nextjs/.next-build/standalone/ /app/servers/nextjs/
COPY --link --from=nextjs-builder /app/servers/nextjs/public /app/servers/nextjs/public
COPY --link --from=nextjs-builder /app/servers/nextjs/.next-build/static /app/servers/nextjs/.next-build/static

# Backend code changes most often; keep it near the end.
COPY --link --from=fastapi-builder /app/servers/fastapi /app/servers/fastapi

COPY --link start.js LICENSE NOTICE ./
COPY --link scripts/presenton-terminal-banner.mjs /app/scripts/presenton-terminal-banner.mjs
COPY --link scripts/user-config-env.cjs /app/scripts/user-config-env.cjs
COPY --link nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
CMD ["node", "/app/start.js"]
