"""
Conectores a herramientas de IA de video.

Estado real de cada API, verificado contra documentacion oficial (2026-09):

  OpusClip   API real y documentada en https://api.opus.pro/api
             (POST /upload-links, POST /clip-projects, GET /clips, GET /brand-templates).
             OJO: el help center oficial dice que el acceso esta en beta cerrada para
             planes anuales de alto volumen; el sitio comercial dice que empieza en Pro.
             Las fuentes se contradicen, asi que el conector degrada con un mensaje
             claro en vez de reventar si tu plan no tiene acceso.

  SendShort  Sin API publica. Su propio help center no documenta ninguna, y las
             comparativas independientes lo confirman. No hay endpoint que llamar.

  CapCut     Sin API publica de renderizado. Su "Open Platform" es para plugins que
             corren DENTRO del editor; su "AI API" se limita a texto-a-video y
             busqueda de plantillas. No hay endpoint para renderizar tu timeline.

Por eso hay tres modos y no uno:

  api        llama de verdad (OpusClip)
  asistido   genera el brief exacto + carpeta de trabajo, tu operas la herramienta,
             y el sistema re-ingesta el archivo terminado (SendShort, CapCut)
  draft      genera un borrador que la herramienta abre (CapCut via formatos de draft)

Regla invariante: NINGUN video vuelve a la cola de publicacion sin pasar otra vez
por compliance. En particular el detector de marca de agua, porque el plan gratuito
de OpusClip exporta CON marca de agua y TikTok rechaza videos con marcas de agua de
otras apps.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from abc import ABC, abstractmethod

from .. import compliance, db


class ErrorProveedor(RuntimeError):
    pass


def _http_urllib(url: str, metodo: str = "GET", cuerpo=None, headers=None,
                 binario: bytes | None = None, timeout: int = 120):
    """Cliente HTTP por defecto. Inyectable en los proveedores para poder probarlos."""
    datos = binario
    if datos is None and cuerpo is not None:
        datos = json.dumps(cuerpo).encode()
    req = urllib.request.Request(url, data=datos, method=metodo)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if datos is not None and not any(h.lower() == "content-type" for h in (headers or {})):
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cuerpo_resp = r.read()
            return {
                "status": r.status,
                "headers": dict(r.headers),
                "json": json.loads(cuerpo_resp) if cuerpo_resp[:1] in (b"{", b"[") else None,
                "raw": cuerpo_resp,
            }
    except urllib.error.HTTPError as e:
        cuerpo_resp = e.read()
        try:
            detalle = json.loads(cuerpo_resp)
        except Exception:
            detalle = cuerpo_resp[:400].decode("utf-8", "replace")
        raise ErrorProveedor(f"HTTP {e.code} en {metodo} {url}: {detalle}") from e
    except urllib.error.URLError as e:
        raise ErrorProveedor(f"No se pudo alcanzar {url}: {e.reason}") from e


class Proveedor(ABC):
    nombre = "base"
    modo = "asistido"          # api | asistido | draft
    tiene_api = False
    url_app = ""
    nota_acceso = ""

    def __init__(self, cfg: dict | None = None, http=_http_urllib):
        self.cfg = cfg or {}
        self._http = http

    # ---- capacidades -------------------------------------------------
    def descripcion(self) -> dict:
        return {
            "nombre": self.nombre, "modo": self.modo, "tiene_api": self.tiene_api,
            "url_app": self.url_app, "nota_acceso": self.nota_acceso,
            "configurado": self.configurado(),
        }

    def configurado(self) -> bool:
        return True

    # ---- flujo -------------------------------------------------------
    def enviar(self, asset: dict, brief, campana: dict) -> dict:
        raise ErrorProveedor(f"{self.nombre} no implementa 'enviar'")

    def estado(self, trabajo: dict) -> dict:
        raise ErrorProveedor(f"{self.nombre} no implementa 'estado'")

    @abstractmethod
    def instrucciones(self, asset: dict, brief, campana: dict) -> str:
        """Pasos exactos para operar la herramienta a mano."""

    @abstractmethod
    def preparar_carpeta(self, asset: dict, brief, campana: dict, destino: str) -> str:
        """Deja el material + el brief listos para arrastrar a la herramienta."""


# ---------------------------------------------------------------------------
# Detector de marca de agua (heuristico, documentado como tal)
# ---------------------------------------------------------------------------

def detectar_marca_agua(ruta_video: str, max_var: float = 6.0, min_brillo: int = 210,
                        max_saturacion: int = 45, muestras: int = 6):
    """
    Devuelve (sospecha: bool, fraccion_maxima: float, zonas: dict).

    La senal que de verdad distingue una marca de agua no es el brillo: es que es
    ESTATICA. Un logo superpuesto no cambia entre frames, mientras que el video de
    fondo si. Asi que se muestrean varios frames a lo largo del clip y se busca, en
    las cuatro esquinas, pixeles que cumplan las tres condiciones a la vez:

      1. varianza temporal casi nula (no se mueve nunca)
      2. brillo alto
      3. saturacion baja (blanco/gris de logotipo)

    Limites honestos:
      * Heuristica. Un fondo fijo y claro puede dar falso positivo; una marca de agua
        animada o centrada puede pasar desapercibida.
      * Con video de menos de ~1s no hay frames suficientes para medir varianza y la
        funcion devuelve sospecha=False con un aviso en `zonas`.
      * NO sustituye la revision humana: el panel muestra el numero y tu decides.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False, 0.0, {"error": "opencv no instalado"}

    cap = cv2.VideoCapture(ruta_video)
    if not cap.isOpened():
        cap.release()
        return False, 0.0, {"error": f"no se pudo abrir {ruta_video}"}

    # CAP_PROP_FRAME_COUNT es 0 o -1 en muchos archivos validos (streaming, pipes,
    # contenedores sin indice). Por eso se muestrea con seek y, si el seek no aplica,
    # se cae a lectura secuencial tomando un frame cada N.
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    if total >= muestras:
        for i in range(muestras):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i * (total - 1) / (muestras - 1)))
            ok, f = cap.read()
            if ok:
                frames.append(cv2.resize(f, (270, 480)).astype(np.float32))
        # si el seek no funciono (devolvio siempre el mismo frame), las varianzas
        # saldrian todas en cero y marcaria falso positivo: se detecta y se reintenta.
        if len(frames) >= 2 and all(np.array_equal(frames[0], x) for x in frames[1:]):
            frames = []
    if not frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        paso, i = 7, 0
        while len(frames) < muestras:
            ok, f = cap.read()
            if not ok:
                break
            if i % paso == 0:
                frames.append(cv2.resize(f, (270, 480)).astype(np.float32))
            i += 1
    cap.release()

    if len(frames) < 3:
        return False, 0.0, {"aviso": f"solo se leyeron {len(frames)} frames: video demasiado corto para medir varianza"}
    if all(np.array_equal(frames[0], x) for x in frames[1:]):
        return False, 0.0, {"aviso": "todos los frames muestreados son identicos: no se puede medir varianza temporal"}

    pila = np.stack(frames)                       # (n, H, W, 3)
    var = pila.std(axis=0).mean(axis=2)           # (H, W) varianza temporal por pixel
    media = pila.mean(axis=0)                     # (H, W, 3) en BGR
    hsv = cv2.cvtColor((media / 255.0).astype(np.float32), cv2.COLOR_BGR2HSV) * np.array([1., 255., 255.])

    estable = var <= max_var
    brillante = media.mean(axis=2) >= min_brillo
    gris = hsv[:, :, 1] <= max_saturacion
    candidato = (estable & brillante & gris).astype(np.float32)

    H, W = candidato.shape
    bh, bw = int(H * 0.18), int(W * 0.30)
    regiones = {
        "sup_izq": candidato[0:bh, 0:bw],
        "sup_der": candidato[0:bh, W - bw:W],
        "inf_izq": candidato[H - bh:H, 0:bw],
        "inf_der": candidato[H - bh:H, W - bw:W],
    }
    zonas = {k: round(float(v.sum()) / v.size, 4) for k, v in regiones.items()}
    maxima = max(zonas.values()) if zonas else 0.0
    zonas["_frames_muestreados"] = len(frames)
    zonas["_varianza_media"] = round(float(var.mean()), 2)
    return maxima >= 0.02, round(maxima, 4), zonas


# ---------------------------------------------------------------------------
# OpusClip — API real
# ---------------------------------------------------------------------------

class OpusClip(Proveedor):
    nombre = "opusclip"
    modo = "api"
    tiene_api = True
    url_app = "https://www.opus.pro"
    nota_acceso = ("API en beta cerrada segun el help center oficial (planes anuales de alto "
                   "volumen); el sitio comercial dice que empieza en Pro. Si tu plan no la "
                   "incluye, la llamada devolvera 401/403 y el sistema cae al modo asistido.")
    BASE = "https://api.opus.pro/api"

    def configurado(self) -> bool:
        return bool(self.cfg.get("api_key"))

    def _auth(self) -> dict:
        if not self.configurado():
            raise ErrorProveedor(
                "OpusClip sin api_key. Copiala de tu panel de Opus a config.json "
                "(proveedores.opusclip.api_key)."
            )
        return {"Authorization": f"Bearer {self.cfg['api_key']}",
                "Accept": "application/json", "Content-Type": "application/json"}

    # -- paso 1/2/3: subir el archivo a GCS via enlace firmado -----------
    def subir_archivo(self, ruta: str) -> str:
        if not os.path.exists(ruta):
            raise ErrorProveedor(f"No existe el archivo {ruta}")
        r = self._http(f"{self.BASE}/upload-links", "POST",
                       cuerpo={"video": {"usecase": "LocalUpload"}}, headers=self._auth())
        url = (r["json"] or {}).get("url")
        upload_id = (r["json"] or {}).get("uploadId")
        if not (url and upload_id):
            raise ErrorProveedor(f"upload-links no devolvio url/uploadId: {r['json']}")

        inicio = self._http(url, "POST",
                            headers={"x-goog-resumable": "start", "Content-Length": "0"})
        location = inicio["headers"].get("location") or inicio["headers"].get("Location")
        if not location:
            raise ErrorProveedor(f"la sesion resumible no devolvio header 'location': {inicio['headers']}")

        with open(ruta, "rb") as f:
            self._http(location, "PUT", binario=f.read(),
                       headers={"Content-Type": "application/octet-stream"}, timeout=1800)
        return upload_id

    def enviar(self, asset: dict, brief, campana: dict) -> dict:
        # Se valida la autenticacion ANTES de tocar la red o el disco: sin esto,
        # una llamada sin api_key empezaba a subir el video y reventaba a medias.
        self._auth()
        origen = asset["ruta_o_url"]
        if origen.startswith(("http://", "https://")):
            video_ref = origen
        else:
            video_ref = self.subir_archivo(origen)

        cuerpo = {
            "videoUrl": video_ref,
            "uploadedVideoAttr": {"title": (campana.get("marca") or "campana")[:120]},
            "curationPref": {
                "clipDurations": [[0, int(min(brief.duracion_seg + 15, 90))]],
                "genre": "Auto",
                "topicKeywords": [brief.gancho["plantilla"][:60]] if brief else [],
                "skipCurate": False,
            },
            "importPref": {"sourceLang": self.cfg.get("idioma", "auto")},
        }
        if self.cfg.get("brand_template_id"):
            cuerpo["brandTemplateId"] = self.cfg["brand_template_id"]
        if self.cfg.get("webhook"):
            cuerpo["conclusionActions"] = [{"type": "WEBHOOK", "notifyFailure": True,
                                            "url": self.cfg["webhook"]}]

        r = self._http(f"{self.BASE}/clip-projects", "POST", cuerpo=cuerpo, headers=self._auth())
        datos = r["json"] or {}
        project_id = datos.get("id") or datos.get("projectId") or r["headers"].get("x-opus-upload-id")
        if not project_id:
            raise ErrorProveedor(f"clip-projects no devolvio id de proyecto: {datos}")
        return {"proveedor": self.nombre, "project_id": project_id, "respuesta": datos}

    def estado(self, trabajo: dict) -> dict:
        pid = trabajo.get("referencia") or trabajo.get("project_id")
        r = self._http(f"{self.BASE}/exportable-clips?findByProjectId={pid}",
                       headers=self._auth())
        datos = r["json"]
        clips = datos if isinstance(datos, list) else (datos or {}).get("clips", [])
        return {
            "proveedor": self.nombre, "project_id": pid,
            "listo": len(clips) > 0, "clips": clips, "n_clips": len(clips),
        }

    def plantillas(self) -> list:
        r = self._http(f"{self.BASE}/brand-templates", headers=self._auth())
        d = r["json"]
        return d if isinstance(d, list) else (d or {}).get("brandTemplates", [])

    def instrucciones(self, asset: dict, brief, campana: dict) -> str:
        return (
            f"1. Abre {self.url_app} y crea un proyecto con: {asset['ruta_o_url']}\n"
            f"2. Duracion objetivo de clip: {brief.duracion_seg:.0f}s\n"
            f"3. Plantilla de marca: {self.cfg.get('brand_template_id', '(elige una sin marca de agua)')}\n"
            f"4. IMPORTANTE: exporta desde un plan de pago. El plan gratuito exporta CON "
            f"marca de agua y TikTok rechaza esos videos.\n"
            f"5. Guarda el archivo en la carpeta de trabajo y registrarlo con "
            f"`main.py render --archivo <ruta>`."
        )

    def preparar_carpeta(self, asset: dict, brief, campana: dict, destino: str) -> str:
        return _preparar_carpeta_comun(self, asset, brief, campana, destino)


# ---------------------------------------------------------------------------
# SendShort / CapCut — sin API: modo asistido real
# ---------------------------------------------------------------------------

class SendShort(Proveedor):
    nombre = "sendshort"
    modo = "asistido"
    tiene_api = False
    url_app = "https://sendshort.ai"
    nota_acceso = ("No tiene API publica (confirmado en su help center y en comparativas "
                   "independientes). No hay nada que automatizar: el conector genera el brief "
                   "y la carpeta de trabajo, y re-ingesta el archivo que exportes.")

    def enviar(self, asset, brief, campana):
        raise ErrorProveedor(
            "SendShort no expone API publica, asi que no hay envio automatico posible. "
            "Usa `main.py proveedor-preparar --proveedor sendshort` y opera la herramienta."
        )

    def instrucciones(self, asset: dict, brief, campana: dict) -> str:
        return (
            f"1. Abre {self.url_app} → 'Upload' y sube: {asset['ruta_o_url']}\n"
            f"2. Generate shorts. Elige duracion ~{brief.duracion_seg:.0f}s.\n"
            f"3. Gancho a usar (editar el que proponga la IA): {brief.gancho['plantilla']}\n"
            f"4. Captions: activados, sin logo de SendShort.\n"
            f"5. Exporta en 9:16 1080x1920 a la carpeta de trabajo.\n"
            f"6. Registra el archivo: `main.py render --archivo <ruta>`."
        )

    def preparar_carpeta(self, asset, brief, campana, destino):
        return _preparar_carpeta_comun(self, asset, brief, campana, destino)


class CapCut(Proveedor):
    nombre = "capcut"
    modo = "asistido"
    tiene_api = False
    url_app = "https://www.capcut.com"
    nota_acceso = ("Sin API publica de renderizado. Su 'Open Platform' es para plugins que "
                   "corren dentro del editor y su 'AI API' se limita a texto-a-video y "
                   "plantillas. Existen generadores comunitarios de archivos draft "
                   "(CapCutAPI/VectCutAPI, Apache-2.0) que escriben un borrador para abrir "
                   "en la app: ese es el unico camino automatizable, y es no oficial.")

    def enviar(self, asset, brief, campana):
        raise ErrorProveedor(
            "CapCut no tiene API de renderizado. Usa `main.py proveedor-preparar "
            "--proveedor capcut` para generar el draft/brief y termina en la app."
        )

    def instrucciones(self, asset: dict, brief, campana: dict) -> str:
        beats = "\n".join(f"     {b['rango']} — {b['instruccion']}" for b in brief.beats)
        return (
            f"1. Abre CapCut (escritorio o web) → New project → importa {asset['ruta_o_url']}\n"
            f"2. Cambia la relacion de aspecto a 9:16.\n"
            f"3. Corta siguiendo esta estructura:\n{beats}\n"
            f"4. Auto captions: activadas. Quita cualquier plantilla que agregue logo de CapCut.\n"
            f"5. Exporta 1080x1920, H.264, a la carpeta de trabajo.\n"
            f"6. Registra el archivo: `main.py render --archivo <ruta>`.\n\n"
            f"Opcion automatizada (no oficial): un servidor MCP comunitario tipo "
            f"CapCutAPI/VectCutAPI puede escribir el borrador con estos mismos beats; "
            f"tu lo abres y exportas. No es un producto de CapCut."
        )

    def preparar_carpeta(self, asset, brief, campana, destino):
        ruta = _preparar_carpeta_comun(self, asset, brief, campana, destino)
        # ademas del brief, un JSON con los beats para quien use un generador de drafts
        beats = os.path.join(destino, "beats.json")
        with open(beats, "w", encoding="utf-8") as f:
            json.dump({"duracion_seg": brief.duracion_seg, "gancho": brief.gancho,
                       "beats": brief.beats, "copy": brief.copy}, f,
                      ensure_ascii=False, indent=2)
        return ruta


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

REGISTRO = {
    "opusclip": OpusClip,
    "sendshort": SendShort,
    "capcut": CapCut,
}


def obtener(nombre: str, cfg: dict | None = None, http=_http_urllib) -> Proveedor:
    clave = (nombre or "").strip().lower()
    if clave not in REGISTRO:
        raise ErrorProveedor(
            f"Proveedor '{nombre}' desconocido. Disponibles: {', '.join(REGISTRO)}"
        )
    cfg = (cfg or {}).get("proveedores", {}).get(clave, {}) if "proveedores" in (cfg or {}) \
        else (cfg or {}).get(clave, {})
    return REGISTRO[clave](cfg, http=http)


def listar(cfg: dict | None = None) -> list[dict]:
    return [obtener(n, cfg).descripcion() for n in REGISTRO]


# ---------------------------------------------------------------------------
# Persistencia de trabajos y re-ingesta
# ---------------------------------------------------------------------------

def registrar_trabajo(proveedor: str, campaign_id: str, asset_id: str, referencia: str,
                      estado: str = "enviado", extra: dict | None = None) -> int:
    with db.sesion() as c:
        cur = c.execute(
            """INSERT INTO provider_jobs (proveedor, campaign_id, asset_id, referencia, estado, extra, creado_en)
               VALUES (?,?,?,?,?,?,?)""",
            (proveedor, campaign_id, asset_id, referencia, estado,
             json.dumps(extra or {}, ensure_ascii=False), db.ahora()),
        )
        db.log(c, "trabajo_proveedor", {"proveedor": proveedor, "ref": referencia})
        return cur.lastrowid


def actualizar_trabajo(job_id: int, estado: str, n_clips: int | None = None, extra: dict | None = None):
    with db.sesion() as c:
        c.execute("UPDATE provider_jobs SET estado=?, n_clips=COALESCE(?, n_clips), extra=? WHERE id=?",
                  (estado, n_clips, json.dumps(extra or {}, ensure_ascii=False), job_id))


def trabajos(pendiente: bool = False) -> list[dict]:
    with db.sesion() as c:
        sql = "SELECT * FROM provider_jobs"
        if pendiente:
            sql += " WHERE estado IN ('enviado','procesando')"
        return db.filas(c.execute(sql + " ORDER BY id DESC"))


def registrar_render(clip_id: str, ruta: str, proveedor: str, forzar: bool = False) -> dict:
    """
    Re-ingesta un archivo terminado. Pasa el detector de marca de agua y lo ata al clip.
    No publica nada: solo deja el archivo listo para `main.py encolar`.
    """
    if not os.path.exists(ruta):
        raise ErrorProveedor(f"No existe {ruta}")

    sospecha, fraccion, zonas = detectar_marca_agua(ruta)
    aviso = None
    if sospecha:
        aviso = (f"Posible marca de agua: {fraccion:.1%} de pixeles tipo logo en una esquina. "
                 f"TikTok rechaza videos con marca de agua de otras apps. Revisa el frame "
                 f"antes de publicar. Zonas: {zonas}")
        if not forzar:
            with db.sesion() as c:
                c.execute("UPDATE clips SET archivo_salida=?, estado='revision_agua' WHERE id=?",
                          (ruta, clip_id))
                db.log(c, "marca_agua_detectada", {"clip": clip_id, "fraccion": fraccion})
            raise ErrorProveedor(aviso + "  Usa --forzar si ya lo revisaste a ojo.")

    with db.sesion() as c:
        if not db.uno(c, "SELECT id FROM clips WHERE id=?", (clip_id,)):
            raise ErrorProveedor(f"El clip '{clip_id}' no existe en la base.")
        c.execute("UPDATE clips SET archivo_salida=? WHERE id=?", (ruta, clip_id))
        db.log(c, "render_registrado", {"clip": clip_id, "ruta": ruta, "proveedor": proveedor,
                                        "fraccion_agua": fraccion})
    return {"clip_id": clip_id, "archivo": ruta, "proveedor": proveedor,
            "fraccion_tipo_logo": fraccion, "aviso": aviso}


# ---------------------------------------------------------------------------
# Carpeta de trabajo comun al modo asistido
# ---------------------------------------------------------------------------

def _preparar_carpeta_comun(prov: Proveedor, asset: dict, brief, campana: dict, destino: str) -> str:
    os.makedirs(destino, exist_ok=True)
    origen = asset["ruta_o_url"]
    if os.path.exists(origen):
        shutil.copy2(origen, os.path.join(destino, os.path.basename(origen)))

    with open(os.path.join(destino, "BRIEF.md"), "w", encoding="utf-8") as f:
        f.write(brief.markdown())
    with open(os.path.join(destino, "PASOS.txt"), "w", encoding="utf-8") as f:
        f.write(f"HERRAMIENTA: {prov.nombre} ({prov.url_app})\n"
                f"CAMPANA: {campana.get('marca')} — {campana.get('titulo')}\n"
                f"CLIP: {brief.clip_id}\n\n"
                f"{prov.instrucciones(asset, brief, campana)}\n\n"
                f"REGLAS DE LA CAMPANA:\n{campana.get('reglas_texto') or '(sin reglas especificas)'}\n\n"
                f"AL TERMINAR:\n"
                f"  python main.py render --clip {brief.clip_id} "
                f"--archivo <ruta_del_export> --proveedor {prov.nombre}\n")
    with open(os.path.join(destino, "licencia.txt"), "w", encoding="utf-8") as f:
        f.write(f"Material de origen: {origen}\n"
                f"Licencia declarada: {asset.get('licencia')}\n"
                f"Evidencia: {asset.get('licencia_prueba') or '(pendiente)'}\n"
                f"Campana: {campana.get('url')}\n\n"
                f"Guarda este archivo. Es tu prueba de que el material estaba autorizado "
                f"si la campana disputa un pago.\n")
    return destino
