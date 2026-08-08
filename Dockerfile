# ---- frontend build ----
FROM node:22-slim AS webbuild
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- runtime ----
FROM python:3.13-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY scripts ./scripts
COPY --from=webbuild /web/dist ./static
ENV STATIC_DIR=/app/static
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
