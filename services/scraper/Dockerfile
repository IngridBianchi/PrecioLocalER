# Imagen del servicio scraper (Python 3.12 + FastAPI).
# Hardening WP1: usuario no-root, sin cache de pip, imagen mínima.
# Uso: docker compose build scraper  ·  docker compose up scraper
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TRUSTED_PROXY_IP=10.99.0.10

WORKDIR /app

# Dependencias primero (capa cacheable). El lockfile fija versiones
# exactas para que la imagen sea reproducible (A06).
COPY pyproject.toml requirements.lock README.md ./
RUN pip install --no-cache-dir hatchling && pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps --no-build-isolation .

COPY app ./app

# non-root: uid/gid explícitos, sin home ni shell.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && chown -R app:app /app
USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

# `--forwarded-allow-ips` con la red interna y NO con "*".
#
# Con "*", uvicorn da por bueno el `X-Forwarded-For` de cualquiera que llegue al
# puerto 8000: un cliente forja la cabecera, uvicorn la aplica como IP de cliente
# y el rate limit se renueva bucket por bucket sin límite real. Ahora solo el
# proxy de entrada (nginx, en la red de compose) puede fijar esa IP, y nginx la
# sobreescribe con `$remote_addr` (ver web/docker/nginx.conf).
#
# El default tiene que coincidir con `web.networks.default.ipv4_address` de
# docker-compose.yml (una sola IP, no la subred: el gateway también está en la
# subred y no debe poder firmar XFF).
#
# Forma shell (no exec) para poder expandir la variable, con `exec` delante
# para que uvicorn reemplace al shell y quede como PID 1: si no, `docker stop`
# le manda SIGTERM al shell, que no lo reenvía, y el contenedor muere por
# SIGKILL a los 10 s cortando scrapes en vuelo. El `:?` hace que no arranque si
# alguien define TRUSTED_PROXY_IP vacío, en vez de que uvicorn se coma el
# `--port` como valor del flag.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers \
    --forwarded-allow-ips ${TRUSTED_PROXY_IP:?falta TRUSTED_PROXY_IP}
