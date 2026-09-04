"""
Capa de publicacion.

Regla de diseno: la publicacion 100% desatendida NO se implementa, y no es una
limitacion tecnica sino una decision documentada:

  1. TikTok Content Posting API — publicar en PUBLICO (DIRECT_POST, scope
     video.publish) requiere que tu app pase la auditoria de TikTok (revision
     manual, semanas). Mientras no la pases, todo sale SELF_ONLY: cero vistas,
     cero pago. PERO el modo borrador (MEDIA_UPLOAD, scope video.upload) no
     depende de esa auditoria: sube el video a la bandeja del creador, TikTok le
     avisa, y el publica desde la app. Ese es el modo que usa este sistema, y es
     justo "subida automatica + autorizacion humana". Ver tiktok_borrador().
  2. Automatizar el navegador para publicar en TikTok/Instagram viola sus ToS y es
     justo el patron que detecta el filtro anti-bot de la campana: el resultado es
     vistas no verificadas y cuenta suspendida. Con baneos se pierde el saldo
     pendiente de pago.
  3. Las campanas exigen divulgacion de contenido comercial; un flujo ciego no la
     aplica de forma fiable.

Lo que si se implementa:
  * modo 'cola'  — deja todo listo (video + copy + hashtags + hora) y TU haces el
                   clic final. Es el modo por defecto y el unico sin riesgo de ban.
  * modo 'api_*' — clientes para las APIs oficiales, con la comprobacion explicita
                   de si la auditoria esta aprobada.
  * modo 'programador' — exporta a Metricolor/Buffer/Later/Postiz, que ya pasaron
                   las auditorias por ti.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from . import compliance, db

RUTA_SALIDA = os.environ.get("CLIPPER_SALIDA", os.path.join(os.path.dirname(__file__), "..", "salida"))


class ErrorPublicacion(RuntimeError):
    pass


def encolar_clip(clip: dict, campana: dict, assets: list[dict], plataforma: str,
                 cuenta: str, reglas: dict | None = None, modo: str = "cola") -> dict:
    """Pasa el clip por compliance y lo deja listo para publicar."""
    with db.sesion() as c:
        if not db.uno(c, "SELECT id FROM clips WHERE id = ?", (clip["id"],)):
            raise ErrorPublicacion(
                f"El clip '{clip['id']}' no existe en la base. Registralo antes "
                "(p.ej. con `main.py brief`), o el bloqueo no quedaria auditado."
            )

    v = compliance.revisar_clip(clip, campana, assets, reglas)

    # El veredicto se persiste en su PROPIA transaccion ya confirmada. Si se
    # escribiera dentro del bloque de abajo y luego se lanzara la excepcion, el
    # rollback del contexto borraria el marcado y el clip rechazado quedaria
    # figurando como 'listo' — que es exactamente el fallo que no puede pasar.
    with db.sesion() as c:
        c.execute(
            "UPDATE clips SET compliance_ok = ?, compliance_motivos = ?, "
            "estado = CASE WHEN ? = 0 THEN 'bloqueado' ELSE estado END WHERE id = ?",
            (1 if v.ok else 0, json.dumps(v.as_dict(), ensure_ascii=False),
             1 if v.ok else 0, clip["id"]),
        )
        db.log(c, "compliance", {"clip": clip["id"], "ok": v.ok, "bloqueos": v.bloqueos})

    if not v.ok:
        raise ErrorPublicacion("Compliance rechazo el clip: " + " | ".join(v.bloqueos))

    with db.sesion() as c:
        pid = f"p_{clip['id']}_{plataforma}"
        c.execute(
            """INSERT INTO posts (id, clip_id, campaign_id, plataforma, cuenta, estado)
               VALUES (?,?,?,?,?,'en_cola')
               ON CONFLICT(id) DO UPDATE SET estado='en_cola'""",
            (pid, clip["id"], campana["id"], plataforma, cuenta),
        )
    return {"post_id": pid, "modo": modo, "compliance": v.as_dict()}


def hora_publicacion(n_post_del_dia: int, minuto_entre_posts: int = 45) -> datetime:
    """Espaciado para no disparar deteccion de spam. Nunca dos posts pegados."""
    base = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return base + timedelta(minutes=minuto_entre_posts * max(0, n_post_del_dia))


def posts_en_cola(plataforma: str | None = None) -> list[dict]:
    with db.sesion() as c:
        if plataforma:
            return db.filas(c.execute(
                "SELECT p.*, cl.copy, cl.archivo_salida FROM posts p JOIN clips cl ON cl.id = p.clip_id "
                "WHERE p.estado='en_cola' AND p.plataforma=? ORDER BY p.id", (plataforma,)))
        return db.filas(c.execute(
            "SELECT p.*, cl.copy, cl.archivo_salida FROM posts p JOIN clips cl ON cl.id = p.clip_id "
            "WHERE p.estado='en_cola' ORDER BY p.id"))


def marcar_publicado(post_id: str, url_post: str, plataforma: str = "manual") -> None:
    with db.sesion() as c:
        c.execute("UPDATE posts SET estado='publicado', url_post=?, publicado_en=? WHERE id=?",
                  (url_post, db.ahora(), post_id))
        c.execute("UPDATE clips SET estado='publicado', enviado_en=? WHERE id=(SELECT clip_id FROM posts WHERE id=?)",
                  (db.ahora(), post_id))
        db.log(c, "publicado", {"post_id": post_id, "url": url_post, "via": plataforma})


def exportar_cola(ruta: str | None = None) -> str:
    """Genera la hoja de trabajo del dia: archivo, copy, hora, checklist."""
    ruta = ruta or os.path.join(RUTA_SALIDA, "cola_de_hoy.json")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    cola = posts_en_cola()
    payload = {
        "generado_en": db.ahora(),
        "posts": [
            {
                "post_id": p["id"],
                "plataforma": p["plataforma"],
                "cuenta": p["cuenta"],
                "archivo_video": p.get("archivo_salida"),
                "copy": p.get("copy"),
                "sugerido_en": hora_publicacion(i).isoformat(),
                "checklist": [
                    "Verifica que el video no tenga marca de agua de la app de edicion",
                    "Confirma que #ad / #sponsored esta visible",
                    "Marca 'contenido de marca' / 'colaboracion pagada' en la propia app",
                    "Publica y pega la URL de vuelta en el sistema",
                    "Envia la URL a la campana ANTES de que se agote el presupuesto",
                ],
            }
            for i, p in enumerate(cola)
        ],
    }
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return ruta


# ---------------------------------------------------------------------------
# Clientes de API oficiales. Usalos solo cuando tengas las credenciales y, en
# TikTok, la auditoria aprobada.
# ---------------------------------------------------------------------------

def publicar_tiktok(video_url_o_ruta: str, titulo: str, privacidad: str = "PUBLIC_TO_EVERYONE",
                    brand_content: bool = True, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    if not cfg.get("access_token"):
        raise ErrorPublicacion("Falta access_token de TikTok. Corre el flujo OAuth primero.")
    if not cfg.get("auditoria_aprobada"):
        raise ErrorPublicacion(
            "Tu app de TikTok NO ha pasado la auditoria: cualquier post saldria SELF_ONLY "
            "(privado, invisible, 0 vistas, 0 pago). Pide el modo 'cola' o usa un programador "
            "ya auditado (Metricool/Buffer/Postiz). No voy a publicar algo que no puede pagarte."
        )
    import urllib.request
    cuerpo = json.dumps({
        "post_info": {
            "title": titulo[:2200],
            "privacy_level": privacidad,
            "disable_duet": False, "disable_stitch": False, "disable_comment": False,
            "brand_content_toggle": bool(brand_content),   # divulgacion de contenido pagado
            "is_aigc": True,                               # etiqueta de contenido con IA
        },
        "source_info": {"source": "PULL_FROM_URL", "video_url": video_url_o_ruta},
    }).encode()
    req = urllib.request.Request(
        "https://open.tiktokapis.com/v2/post/publish/video/init/", data=cuerpo,
        headers={"Content-Type": "application/json; charset=UTF-8",
                 "Authorization": f"Bearer {cfg['access_token']}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    if resp.get("error", {}).get("code") != "ok":
        raise ErrorPublicacion(f"TikTok rechazo la publicacion: {resp.get('error')}")
    with db.sesion() as c:
        db.log(c, "publicado_api_tiktok", resp.get("data", {}).get("publish_id"))
    return resp.get("data", {})


# ---------------------------------------------------------------------------
# Modo borrador de TikTok — el que hace posible "automatico + yo autorizo"
# ---------------------------------------------------------------------------

def tiktok_borrador(video_url_o_ruta: str, cfg: dict | None = None,
                    es_url: bool = True) -> dict:
    """
    Sube el video a la bandeja/borradores de TikTok con post_mode=MEDIA_UPLOAD.

    Por que esto es distinto del direct post:
      * Usa el scope `video.upload`, no `video.publish`. El direct post exige que
        tu app pase la auditoria de TikTok; mientras no la pase, todo sale SELF_ONLY
        (privado, 0 vistas). El modo borrador no depende de esa auditoria.
      * TikTok avisa al creador en la app y el publica desde ahi. O sea: la subida
        es automatica y la decision sigue siendo tuya. Es exactamente el flujo de
        "modo cola con autorizacion", no un arreglo a medias.

    Limitacion real de TikTok que hay que saber: en MEDIA_UPLOAD la API NO permite
    fijar titulo, caption ni privacidad. El video llega a tu bandeja y el copy se
    pone dentro de la app. Por eso el sistema te manda el copy sugerido en la
    notificacion, para que lo copies.

    Extra que vale la pena: varios integradores documentan que los clips terminados
    y publicados desde dentro de la app tienden a tener MAS alcance que los
    publicados directo por API, porque ahi puedes agregar el audio en tendencia.
    """
    cfg = cfg or {}
    token = cfg.get("access_token")
    if not token:
        raise ErrorPublicacion(
            "Falta el access_token de TikTok. Corre el flujo OAuth con el scope "
            "`video.upload` (no necesitas `video.publish` ni la auditoria para el "
            "modo borrador)."
        )
    import urllib.request

    if es_url:
        source_info = {"source": "PULL_FROM_URL", "video_url": video_url_o_ruta}
    else:
        # FILE_UPLOAD requiere subir el archivo al chunk_upload_url que devuelve
        # esta misma llamada; se hace en publicar_archivo_tiktok().
        source_info = {"source": "FILE_UPLOAD"}

    cuerpo = json.dumps({
        "post_info": {
            "post_mode": "MEDIA_UPLOAD",        # ← bandeja del creador, no publico
            "title": "",                        # TikTok lo ignora en este modo
            "privacy_level": "SELF_ONLY",
            "brand_content_toggle": True,       # divulgacion de contenido pagado
            "is_aigc": True,                    # etiqueta de contenido con IA
        },
        "source_info": source_info,
    }).encode()
    req = urllib.request.Request(
        "https://open.tiktokapis.com/v2/post/publish/video/init/", data=cuerpo,
        headers={"Content-Type": "application/json; charset=UTF-8",
                 "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    if resp.get("error", {}).get("code") != "ok":
        raise ErrorPublicacion(f"TikTok rechazo la subida a borradores: {resp.get('error')}")
    with db.sesion() as c:
        db.log(c, "tiktok_borrador", resp.get("data", {}))
    return resp.get("data", {})


def tiktok_estado(public_id: str, cfg: dict | None = None) -> dict:
    """
    Consulta el estado. Los valores relevantes de TikTok:
      SEND_TO_USER_INBOX  el borrador ya esta en la bandeja del creador
      PUBLISH_COMPLETE    publicado
      FAILED              fallo, ver fail_reason
    """
    cfg = cfg or {}
    if not cfg.get("access_token"):
        raise ErrorPublicacion("Falta el access_token de TikTok.")
    import urllib.request
    req = urllib.request.Request(
        "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
        data=json.dumps({"publish_id": publish_id}).encode(),
        headers={"Content-Type": "application/json; charset=UTF-8",
                 "Authorization": f"Bearer {cfg['access_token']}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def publicar_instagram(video_url: str, copy: str, cfg: dict | None = None) -> dict:
    cfg = cfg or {}
    if not (cfg.get("access_token") and cfg.get("ig_user_id")):
        raise ErrorPublicacion("Falta ig_user_id o access_token de Instagram Graph API.")
    import urllib.parse
    import urllib.request

    def _post(url: str, data: dict) -> dict:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode())
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())

    base = "https://graph.facebook.com/v19.0"
    cont = _post(f"{base}/{cfg['ig_user_id']}/media", {
        "media_type": "REELS", "video_url": video_url, "caption": copy[:2200],
        "share_to_feed": "true", "access_token": cfg["access_token"],
    })
    pub = _post(f"{base}/{cfg['ig_user_id']}/media_publish", {
        "creation_id": cont["id"], "access_token": cfg["access_token"],
    })
    with db.sesion() as c:
        db.log(c, "publicado_api_instagram", pub)
    return pub
