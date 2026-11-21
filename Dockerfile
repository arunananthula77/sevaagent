FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt \
    && playwright install --with-deps chromium \
    && chmod -R a+rX /opt/pw
COPY backend/ backend/
COPY --from=web /web/dist frontend/dist
RUN useradd -m -u 1000 user && chown -R user /app
USER user
ENV SEVA_BASE_URL=http://127.0.0.1:7860 GROQ_MODEL=openai/gpt-oss-120b
EXPOSE 7860
WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
