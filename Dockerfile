# Clipping OS — imagen minima.
# El nucleo no tiene dependencias obligatorias (solo libreria estandar de Python),
# asi que la imagen queda chica y sin paso de build.
FROM python:3.12-slim

# ffmpeg y Pillow solo si usas el pipeline de video local (proveedores que
# devuelven un archivo en vez de subirlo ellos). Se pueden quitar si no lo usas.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true
COPY . .

# La base y los secretos van en un volumen: si se pierden al reiniciar, pierdes
# el historial de pagos. En un tier gratuito sin disco persistente esto es lo
# primero que se rompe.
ENV CLIPPER_DB=/data/clipping.db \
    CLIPPER_SALIDA=/data/salida \
    CLIPPER_SECRETS_DIR=/data \
    PORT=8000
VOLUME ["/data"]

EXPOSE 8000
CMD ["sh", "-c", "python main.py panel --puerto ${PORT:-8000}"]
