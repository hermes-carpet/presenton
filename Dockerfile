# syntax=docker/dockerfile:1.7

FROM python:3.11-slim-trixie AS fastapi-builder

WORKDIR /app/servers/fastapi

ENV UV_LINK_MODE=copy

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

# Fork: slim the venv. Wheels ship native libraries with debug symbols
# (~175 MB), and many packages bundle their test suites.
RUN apt-get update && apt-get install -y --no-install-recommends binutils \
    && rm -rf /var/lib/apt/lists/* \
    && find /opt/venv -type f -name "*.so*" -exec sh -c 'strip --strip-unneeded "$@" 2>/dev/null || true' _ {} + \
    && find /opt/venv/lib/python3*/site-packages -depth -type d \( -name tests -o -name test \) -exec rm -rf {} +

# Fork: compile the venv's bytecode here, before the code copy, so it only
# changes with uv.lock and containers don't write it at runtime. Hash-based
# pycs are byte-identical across rebuilds; "unchecked" skips revalidation, as
# the venv is immutable. (A few vendored files with legacy syntax don't
# compile; that is harmless.)
RUN /opt/venv/bin/python -m compileall -q -j 0 --invalidation-mode unchecked-hash /opt/venv/lib >/dev/null || true

# Fork: bake mem0's default embedding model into the image. mem0 creates
# fastembed's TextEmbedding without a cache_dir, so fastembed uses
# FASTEMBED_CACHE_PATH, else /tmp/fastembed_cache; upstream's warm-up wrote it
# there in the builder only, so every container downloaded it at runtime.
# Done before the code copy, so this layer only changes with uv.lock.
ENV FASTEMBED_CACHE_PATH=/root/.cache/fastembed
RUN PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python -c "from fastembed import TextEmbedding; next(TextEmbedding(model_name='BAAI/bge-small-en-v1.5').embed(['warmup']))" \
    && rm -rf /root/.cache/huggingface/xet/logs

# The backend project is not installed into the venv. A constant .pth file puts
# /app/servers/fastapi on sys.path instead (same import order as an installed
# package), so /opt/venv changes only with uv.lock and code-only updates don't
# rewrite the venv layer.
RUN echo /app/servers/fastapi > "$(/opt/venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/presenton-backend.pth"

COPY servers/fastapi /app/servers/fastapi
ENV HF_HOME=/root/.cache/huggingface \
    PRESENTON_FASTEMBED_ICON_CACHE_DIR=/root/.cache/presenton/fastembed-icons
# Warm FastEmbed caches into the image (not a BuildKit cache mount, or HF weights would be missing).
# Fork: PYTHONDONTWRITEBYTECODE keeps /opt/venv byte-identical across code
# changes, so its image layer is reused.
RUN PYTHONDONTWRITEBYTECODE=1 /opt/venv/bin/python scripts/warm_fastembed_cache.py \
    # Fork: drop huggingface_hub's timestamped download logs (the only files it
    # leaves under HF_HOME), so the copied cache dir is identical across builds.
    && rm -rf /root/.cache/huggingface/xet/logs

# Fork: move the large, rarely changing data dirs (icons, icon index) out so the
# runtime stage copies them as separate layers and a code-only update ships
# only the code.
RUN mkdir -p /app/fastapi-data/static /app/fastapi-data/assets \
    && for d in static assets; do \
    if [ -d "$d" ]; then rmdir "/app/fastapi-data/$d" && mv "$d" /app/fastapi-data/; fi; \
    done

# Fork: compile the backend too ("checked" hash pycs, so a mounted override of
# a source file is never shadowed by stale bytecode).
RUN /opt/venv/bin/python -m compileall -q -j 0 --invalidation-mode checked-hash /app/servers/fastapi >/dev/null || true


FROM node:22-bookworm-slim AS nextjs-builder

WORKDIR /app/servers/nextjs

ENV NEXT_TELEMETRY_DISABLED=1

COPY servers/nextjs/package.json servers/nextjs/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci

COPY servers/nextjs /app/servers/nextjs
RUN npm run build \
    && rm -rf .next-build/cache \
    # Fork: public/ is copied separately in the runtime stage (the standalone
    # copy is a duplicate subset), and the image is glibc-only.
    && rm -rf .next-build/standalone/public .next-build/standalone/node_modules/@img/*linuxmusl* \
    # Fork: node_modules gets its own runtime layer (see the runtime stage).
    && mv .next-build/standalone/node_modules /app/nextjs-node_modules


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

# Fork: no source maps, type definitions or sharp's wasm fallback at runtime;
# LiteParse runs from dist/, so drop its src/ copy and tests. tesseract.js in
# Node loads tesseract-core-*.js + .wasm; *.wasm.js are inlined browser builds.
RUN find /app/presentation-export /app/document-extraction-liteparse -type f \( -name "*.map" -o -name "*.d.ts" \) -delete \
    && rm -rf /app/presentation-export/node_modules/@img/sharp-wasm32 /app/document-extraction-liteparse/node_modules/@img/sharp-wasm32 \
    && rm -rf /app/document-extraction-liteparse/node_modules/@llamaindex/liteparse/src \
    && find /app/document-extraction-liteparse/node_modules/@llamaindex/liteparse/dist -name "*.test.js" -delete \
    && cd /app/document-extraction-liteparse/node_modules/tesseract.js-core \
    && rm -f ./*.wasm.js

# Fork: LiteParse and Next.js often pin the same sharp/libvips; when the
# package versions match, LiteParse links to Next.js's copy instead of
# shipping a second one (~17 MB).
COPY --from=nextjs-builder /app/nextjs-node_modules/@img /tmp/nextjs-img
RUN for src in /tmp/nextjs-img/sharp-libvips-*; do \
    name=$(basename "$src"); dest=/app/document-extraction-liteparse/node_modules/@img/$name; \
    if [ -d "$dest" ] && cmp -s "$src/package.json" "$dest/package.json"; then \
    rm -rf "$dest" && ln -s "/app/servers/nextjs/node_modules/@img/$name" "$dest"; \
    fi; \
    done; \
    rm -rf /tmp/nextjs-img

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
    && ln -s "$(find /opt/chrome-headless-shell -type f -name chrome-headless-shell)" /opt/chrome-headless-shell/headless-shell \
    && find /opt/chrome-headless-shell -path "*/locales/*" -type f ! -name "en-US.pak" -delete; \
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
    PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chrome-headless-shell \
    LITEPARSE_TESSDATA_PATH=/usr/share/tessdata \
    FASTEMBED_CACHE_PATH=/root/.cache/fastembed

RUN set -eux; \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot; \
    printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/%s trixie-security main\n' "$CHROMIUM_SNAPSHOT" > /etc/apt/sources.list.d/chromium-snapshot.list; \
    # Fork: headless-only Chrome for exports (PDF/PPTX, template previews) and no
    # desktop/GUI stack. Fonts: Latin and symbols plus emoji as fallbacks; slides
    # use their own web fonts.
    packages="ca-certificates curl nginx fontconfig imagemagick zstd \
    fonts-noto-core fonts-noto-color-emoji"; \
    # Fork: LiteParse OCRs with tesseract.js (WASM), not the tesseract binary; it
    # only needs the language data (LITEPARSE_TESSDATA_PATH below), which also
    # keeps OCR working offline instead of fetching models from a CDN.
    if [ "$INSTALL_TESSERACT" = "true" ]; then packages="$packages tesseract-ocr-eng"; fi; \
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
    if [ "$INSTALL_TESSERACT" = "true" ]; then \
    ln -s "$(dirname "$(find /usr/share/tesseract-ocr -name eng.traineddata | head -1)")" /usr/share/tessdata; \
    fi; \
    # Fork: Node runtime only; headers (~56 MB) and npm/corepack are unused in
    # production (start.js runs npm only with --dev).
    printf '%s\n' 'path-exclude=/usr/include/node/*' 'path-exclude=/usr/lib/node_modules/npm/*' \
    'path-exclude=/usr/lib/node_modules/corepack/*' 'path-exclude=/usr/bin/npm' 'path-exclude=/usr/bin/npx' \
    'path-exclude=/usr/bin/corepack' > /etc/dpkg/dpkg.cfg.d/presenton-node-runtime; \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    # Fork: Node ships with debug info (~17 MB).
    apt-get install -y --no-install-recommends binutils; \
    strip --strip-unneeded /usr/bin/node; \
    apt-get purge -y --auto-remove binutils; \
    rm -rf /var/lib/apt/lists/*

# Fork: the official Python image ships its stdlib without bytecode, so every
# container would compile it at runtime; do it once here (changes only with the
# base image).
RUN python -m compileall -q -j 0 --invalidation-mode unchecked-hash "$(python -c 'import sysconfig; print(sysconfig.get_paths()["stdlib"])')" >/dev/null || true

# Remove any non-Noto fonts that may have been installed as dependencies.
RUN find /usr/share/fonts -type f ! -iname 'Noto*' -delete \
    && find /usr/share/fonts -type d -empty -delete \
    && fc-cache -fsv

# Fork: headless browser for exports. Fail the build if it can't resolve a
# library, then render once so its bundled fontconfig writes its caches into
# the image instead of into every container. Placed before any code-dependent
# layer so it is rebuilt only with the browser or system packages.
COPY --link --from=assets-builder /opt/chrome-headless-shell /opt/chrome-headless-shell
RUN ! ldd "$(readlink -f "$PUPPETEER_EXECUTABLE_PATH")" | grep "not found" \
    && "$PUPPETEER_EXECUTABLE_PATH" --version \
    && "$PUPPETEER_EXECUTABLE_PATH" --no-sandbox --disable-gpu --dump-dom \
    "data:text/html,<p style='font-family:sans-serif'>x &#10003; &#128640;</p>" >/dev/null \
    && rm -rf /tmp/* /root/.config /root/.cache/chromium /root/.pki

RUN mkdir -p /app/scripts /app/servers/fastapi /app/servers/nextjs
RUN mkdir -p /app_data/exports /app_data/images /app_data/uploads /app_data/fonts /app_data/templates /app_data/pptx-to-html /app_data/pptx-to-json \
    && chmod -R a+rX /app_data

# Runtime copies use --link so each layer is reused whenever its own content is
# unchanged, even if earlier layers changed (e.g. a Chromium bump or a backend
# code update); pulls then only fetch what actually changed.
COPY --link --from=fastapi-builder /opt/venv /opt/venv
COPY --link --from=fastapi-builder /root/.cache/huggingface /root/.cache/huggingface
COPY --link --from=fastapi-builder /root/.cache/presenton/fastembed-icons /root/.cache/presenton/fastembed-icons
COPY --link --from=fastapi-builder /root/.cache/fastembed /root/.cache/fastembed
COPY --link templates /app/templates

COPY --link --from=assets-builder /app/package.json /app/package.json
COPY --link --from=assets-builder /app/document-extraction-liteparse /app/document-extraction-liteparse
COPY --link --from=assets-builder /app/presentation-export /app/presentation-export
COPY --link --from=assets-builder /app/scripts/sync-presentation-export.cjs /app/scripts/sync-presentation-export.cjs

RUN test -f /app/presentation-export/runner.mjs \
    && test -f /app/presentation-export/node_modules/@presenton/export-core/dist/index.js

COPY --link --from=nextjs-builder /app/nextjs-node_modules /app/servers/nextjs/node_modules
COPY --link --from=nextjs-builder /app/servers/nextjs/.next-build/standalone/ /app/servers/nextjs/
COPY --link --from=nextjs-builder /app/servers/nextjs/public /app/servers/nextjs/public
COPY --link --from=nextjs-builder /app/servers/nextjs/.next-build/static /app/servers/nextjs/.next-build/static

# Backend code changes most often; keep it near the end, with its large data
# dirs in their own layers.
COPY --link --from=fastapi-builder /app/fastapi-data/static /app/servers/fastapi/static
COPY --link --from=fastapi-builder /app/fastapi-data/assets /app/servers/fastapi/assets
COPY --link --from=fastapi-builder /app/servers/fastapi /app/servers/fastapi

COPY --link start.js LICENSE NOTICE ./
COPY --link scripts/presenton-terminal-banner.mjs /app/scripts/presenton-terminal-banner.mjs
COPY --link scripts/user-config-env.cjs /app/scripts/user-config-env.cjs
COPY --link nginx.conf /etc/nginx/nginx.conf

EXPOSE 80
CMD ["node", "/app/start.js"]
