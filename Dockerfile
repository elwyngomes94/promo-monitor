FROM python:3.12-slim
WORKDIR /app
COPY promo ./promo
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 PROMO_DB=/data/promo.db
RUN mkdir -p /data
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["python", "-m", "promo", "serve", "--collect", "--demo-if-empty"]
