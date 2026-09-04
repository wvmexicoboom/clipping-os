# Despliegue en un servidor gratuito

## Lo que este proyecto necesita de un host

| Requisito | Por qué | Qué pasa si el host no lo cumple |
|---|---|---|
| **Python 3.10+** | Todo el núcleo es librería estándar | No arranca |
| **Disco persistente** | La base SQLite guarda el historial de pagos | **Pierdes el registro de cuánto cobraste** al reiniciar |
| **Proceso que no se duerma** | `main.py auto --vigilar` corre en ciclo | El ciclo se detiene y deja de producir |
| **Puerto configurable por `$PORT`** | Así lo inyectan Render/Railway/Fly | El panel no recibe tráfico |
| Salida saliente a Internet | Llama a OpusClip, TikTok, Telegram | No produce ni avisa |

Dos de esos cinco son los que rompen los tiers gratuitos: el **disco persistente** y el
**proceso que no se duerma**. Verifícalos antes de elegir, no después.

## Archivos ya preparados

```
Dockerfile       imagen python:3.12-slim + ffmpeg, volumen en /data
Procfile         web: python main.py panel --puerto $PORT   (Buildpack de Python)
runtime.txt      python-3.12
.dockerignore    excluye demo/, secretos y caches
```

`requirements.txt` está **vacío de dependencias obligatorias**: el núcleo corre con la
librería estándar. Eso elimina el paso de build y hace que cualquier host gratuito sirva.

Las rutas las fijan variables de entorno, así que el mismo código corre en tu máquina
y en el servidor sin tocar nada:

```
CLIPPER_DB           ruta de la base SQLite
CLIPPER_SALIDA       carpeta de clips producidos
CLIPPER_SECRETS_DIR  carpeta de secrets.enc
PORT                 puerto que escucha el panel
```

## Camino A — Docker (recomendado, sirve en casi cualquier host)

```bash
docker build -t clipping-os .
docker run -d --name clipping -p 8000:8000 \
  -e CLIPPER_PASSPHRASE='una frase larga y tuya' \
  -v clipping-data:/data \
  clipping-os
```

El volumen `clipping-data` es lo que conserva tu base entre reinicios. **Sin él, cada
reinicio borra el historial de pagos.**

## Camino B — Buildpack de Python (Render, Railway, Heroku-like)

Usa `Procfile` + `runtime.txt`. Apunta el comando a:

```
python main.py panel --puerto $PORT
```

## Camino C — VPS barato con systemd

```ini
# /etc/systemd/system/clipping.service
[Unit]
Description=Clipping OS
After=network.target

[Service]
WorkingDirectory=/opt/clipping-os
Environment=CLIPPER_DB=/var/lib/clipping/clipping.db
Environment=CLIPPER_SALIDA=/var/lib/clipping/salida
Environment=CLIPPER_SECRETS_DIR=/var/lib/clipping
Environment=CLIPPER_PASSPHRASE=una frase larga y tuya
ExecStart=/usr/bin/python3 main.py panel --puerto 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

## El ciclo autónomo es un proceso aparte

El panel atiende la web; el ciclo que produce clips va separado:

```bash
python3 main.py auto --vigilar --proveedor capcut
```

En Docker: un segundo servicio con ese comando. En un VPS: otra unidad de systemd.
**Un tier gratuito que duerme el proceso mata este ciclo**, aunque el panel siga
respondiendo cuando lo despiertas.

## El receptor de botones es un tercer proceso

Los botones de Telegram no llegan solos: hay que ir a buscarlos con `getUpdates`.

```bash
python3 main.py telegram-escuchar
```

Si este proceso no corre, **los botones del mensaje no hacen nada** (aunque el panel
y el ciclo sigan vivos). Alternativa sin proceso extra: aprobar en `/autorizar`.
El offset se guarda en `bot_state`, asi que al reiniciar no se reprocesa una
decision ya tomada.

## Antes de exponerlo a Internet

El panel **no tiene autenticación**. Está pensado para `localhost` o una red privada.
Si lo expones públicamente:

1. Ponlo detrás de un reverse proxy con auth básica (Caddy o nginx + `htpasswd`).
2. Oponle un túnel con acceso restringido (Tailscale, Cloudflare Access).
3. **No** lo abras a `0.0.0.0` sin nada delante: muestra tus campañas, tus cuentas y
   permite aprobar publicaciones.

La página `/autorizar` sí lleva token por pendiente, pero eso protege la decisión de
publicar, no la lectura del panel.

## Verificación después de desplegar

```bash
curl -o /dev/null -w "%{http_code}\n" https://tu-host/            # 200
curl -o /dev/null -w "%{http_code}\n" https://tu-host/autorizar   # 200
curl -X POST https://tu-host/api/autorizar \
     -H 'Content-Type: application/json' -d '{"token":"x","aprobar":true}'   # 403
```

El `403` es la prueba de que la autorización por token está activa.

## Lo que no puedo verificar desde aquí

No tengo acceso a los planes vigentes de ningún proveedor de hosting, así que no puedo
afirmar cuál tiene tier gratuito hoy ni cuáles son sus límites actuales. Esa parte
tienes que confirmarla tú al contratar. Lo que sí verifiqué: que el proyecto arranca
con las variables de entorno del `Dockerfile`, que escucha en el puerto que se le
inyecta, y que no tiene dependencias que instalar.
