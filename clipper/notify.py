"""
Notificaciones y autorizacion de publicacion.

El flujo que se implementa aqui es el de "modo cola con aviso":

    el sistema produce el clip
        → lo deja pendiente
        → te avisa por Telegram con el VIDEO adjunto y el copy sugerido
        → tu tocas "Aprobar" (o abres el panel y lo apruebas ahi)
        → solo entonces queda listo para publicarse

Por que Telegram y no otra cosa:
  * La Bot API es gratuita y no requiere aprobacion de ninguna app.
  * `sendVideo` permite adjuntar el clip (limite 50 MB; un clip 9:16 de 20 s
    pesa unos pocos MB, asi que cabe de sobra).
  * `InlineKeyboardMarkup` da botones de Aprobar / Rechazar en el mismo mensaje.
  * Limite real: ~1 mensaje por segundo por chat. Irrelevante para este volumen.

Tambien hay un canal generico por webhook, por si prefieres Slack/Discord/ntfy.

Seguridad: cada pendiente lleva un `token` aleatorio de 32 bytes. Aprobar por URL
sin el token correcto no hace nada. El panel y el bot exigen el mismo token.
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
import urllib.error
import urllib.parse
import urllib.request

from . import db

TELEGRAM_API = "https://api.telegram.org"
LIMITE_MB_TELEGRAM = 50
LIMITE_MENSAJE = 4096      # sendMessage
LIMITE_CAPTION = 1024      # sendVideo


def _cortar(texto: str, limite: int) -> str:
    """Recorta al limite de Telegram sin dejar un mensaje a medias."""
    if len(texto) <= limite:
        return texto
    return texto[: limite - 1].rstrip() + "…"


class ErrorNotificacion(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Transporte HTTP inyectable (para poder probar sin tocar la red)
# ---------------------------------------------------------------------------

def _http(url: str, datos=None, archivos: dict | None = None, timeout: int = 120):
    """POST multipart si hay archivos; si no, POST con campos.

    Dos detalles que rompen en produccion si no se cuidan:
      * Un campo no puede ir como texto Y como archivo: Telegram se queda con el
        primero y recibe la ruta en vez del video. Se excluyen los duplicados.
      * Los valores deben ser texto: un chat_id numerico sin convertir revienta
        el urlencode.
    """
    datos = {k: (v if isinstance(v, str) else str(v))
             for k, v in (datos or {}).items() if k not in (archivos or {})}
    if archivos:
        limite = b"----clippingos"
        cuerpo = b""
        for k, v in datos.items():
            cuerpo += f"--{limite.decode()}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, ruta in archivos.items():
            with open(ruta, "rb") as f:
                contenido = f.read()
            nombre = os.path.basename(ruta)
            cuerpo += (f"--{limite.decode()}\r\nContent-Disposition: form-data; "
                       f"name=\"{k}\"; filename=\"{nombre}\"\r\n"
                       f"Content-Type: video/mp4\r\n\r\n").encode() + contenido + b"\r\n"
        cuerpo += f"--{limite.decode()}--\r\n".encode()
        req = urllib.request.Request(url, data=cuerpo, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={limite.decode()}")
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(datos).encode(),
                                     method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detalle = e.read()[:400].decode("utf-8", "replace")
        raise ErrorNotificacion(f"HTTP {e.code} de Telegram: {detalle}") from e
    except urllib.error.URLError as e:
        raise ErrorNotificacion(f"No se pudo alcanzar Telegram: {e.reason}") from e


# ---------------------------------------------------------------------------
# Pendientes de autorizacion
# ---------------------------------------------------------------------------

def nuevo_pendiente(post_id: str, canal: str = "telegram", nota: str = "") -> str:
    """Crea un pendiente de autorizacion y devuelve su token."""
    token = _secrets.token_urlsafe(32)
    with db.sesion() as c:
        if not db.uno(c, "SELECT id FROM posts WHERE id=?", (post_id,)):
            raise ErrorNotificacion(f"El post '{post_id}' no existe en la base.")
        c.execute("""INSERT INTO approvals (post_id, token, estado, canal, creado_en, nota)
                     VALUES (?,?,'pendiente',?,?,?)
                     ON CONFLICT(post_id) DO UPDATE SET
                       token=excluded.token, estado='pendiente', nota=excluded.nota,
                       creado_en=excluded.creado_en""",
                  (post_id, token, canal, db.ahora(), nota))
        c.execute("UPDATE posts SET estado='pendiente_autorizacion' WHERE id=?", (post_id,))
        db.log(c, "autorizacion_solicitada", {"post_id": post_id})
    return token


def pendientes() -> list[dict]:
    with db.sesion() as c:
        rows = db.filas(c.execute(
            """SELECT a.*, p.plataforma, p.cuenta, p.campaign_id, p.clip_id,
                      cl.copy AS copy, cl.archivo_salida AS archivo, cl.hook AS gancho,
                      cl.titulo AS titulo, cl.duracion_seg AS duracion_seg,
                      cm.marca AS marca, cm.categoria AS categoria
               FROM approvals a
               JOIN posts p ON p.id = a.post_id
               JOIN clips cl ON cl.id = p.clip_id
               JOIN campaigns cm ON cm.id = p.campaign_id
               WHERE a.estado='pendiente' ORDER BY a.creado_en"""))
    return rows


def kit_de(pend: dict) -> dict:
    """Kit de publicacion de un pendiente: titulos, descripcion, hashtags, etc."""
    from . import viral
    return viral.generar_kit(
        {"id": pend.get("clip_id") or pend.get("post_id"), "titulo": pend.get("titulo"),
         "hook": pend.get("gancho"), "copy": pend.get("copy"),
         "duracion_seg": pend.get("duracion_seg")},
        {"marca": pend.get("marca"), "categoria": pend.get("categoria")},
        pend.get("plataforma", "tiktok"),
    )


def autorizar(post_id: str, token: str, aprobado: bool, motivo: str = "") -> dict:
    """Aprueba o rechaza. Exige que el token coincida: sin eso no hace nada."""
    with db.sesion() as c:
        reg = db.uno(c, "SELECT * FROM approvals WHERE post_id=?", (post_id,))
        if not reg:
            raise ErrorNotificacion(f"No hay solicitud pendiente para '{post_id}'.")
        if not _secrets.compare_digest(str(reg["token"]), str(token or "")):
            db.log(c, "autorizacion_token_invalido", {"post_id": post_id})
            raise ErrorNotificacion("Token incorrecto: autorizacion rechazada.")
        nuevo = "aprobado" if aprobado else "rechazado"
        c.execute("UPDATE approvals SET estado=?, resuelto_en=?, motivo=? WHERE post_id=?",
                  (nuevo, db.ahora(), motivo, post_id))
        c.execute("UPDATE posts SET estado=? WHERE id=?",
                  ("en_cola" if aprobado else "rechazado", post_id))
        db.log(c, "autorizacion_resuelta", {"post_id": post_id, "estado": nuevo, "motivo": motivo})
    return {"post_id": post_id, "estado": nuevo}


def autorizar_por_token(token: str, aprobado: bool) -> dict:
    """Para el boton de Telegram, que solo conoce el token."""
    with db.sesion() as c:
        reg = db.uno(c, "SELECT post_id FROM approvals WHERE token=? AND estado='pendiente'", (token,))
    if not reg:
        raise ErrorNotificacion("Ese enlace ya se uso o no existe.")
    return autorizar(reg["post_id"], token, aprobado)


# ---------------------------------------------------------------------------
# Canales
# ---------------------------------------------------------------------------

def _teclado(token: str, url_panel: str = "") -> dict:
    fila = [
        {"text": "✅ Aprobar", "callback_data": f"ok:{token}"},
        {"text": "❌ Rechazar", "callback_data": f"no:{token}"},
    ]
    teclado = [fila]
    if url_panel:
        teclado.append([{"text": "🖥 Abrir panel", "url": url_panel}])
    return {"inline_keyboard": teclado}


def avisar_telegram(post: dict, cfg: dict, http=_http, url_panel: str = "") -> dict:
    """
    Manda el clip por Telegram con el copy sugerido y botones de decision.

    Ojo con un detalle real del modo borrador de TikTok: la API no permite fijar
    el caption en ese modo, asi que el copy va en el mensaje para que lo copies
    dentro de la app. No es un descuido, es una restriccion de TikTok.
    """
    tg = (cfg.get("notificaciones") or {}).get("telegram") or {}
    token_bot, chat_id = tg.get("bot_token"), tg.get("chat_id")
    if not (token_bot and chat_id):
        raise ErrorNotificacion(
            "Telegram sin configurar. Guarda el token del bot y tu chat_id: "
            "`main.py secretos-set --proveedor telegram --campo bot_token` y `--campo chat_id`."
        )
    from .secrets import obtener
    token_bot = token_bot or obtener("telegram", "bot_token")
    chat_id = chat_id or obtener("telegram", "chat_id")

    archivo = post.get("archivo")
    # El kit va en el mensaje porque en modo borrador de TikTok la API no permite
    # fijar el caption: titulo, descripcion y hashtags se copian dentro de la app.
    try:
        from . import viral
        kit_txt = viral.kit_a_texto(kit_de(post))
    except Exception as e:  # el aviso no debe fallar por el kit
        kit_txt = f"(kit no disponible: {e})"
    cabecera = (f"🎬 Clip listo — {post.get('plataforma','?')} / {post.get('cuenta','')}\n"
                f"Campana: {post.get('campaign_id','')}\n\n{kit_txt}")

    if archivo and os.path.exists(archivo) and \
       os.path.getsize(archivo) <= LIMITE_MB_TELEGRAM * 1024 * 1024:
        try:
            k = kit_de(post)
            caption_vid = _cortar(f"{k['titulo_recomendado']}\n\n{k['descripcion']}", LIMITE_CAPTION)
        except Exception:
            caption_vid = _cortar(cabecera, LIMITE_CAPTION)
        r = http(f"{TELEGRAM_API}/bot{token_bot}/sendVideo",
                 datos={"chat_id": chat_id, "caption": caption_vid,
                        "supports_streaming": "true",
                        "reply_markup": json.dumps(_teclado(post["token"], url_panel))},
                 archivos={"video": archivo})
    else:
        nota = "" if not archivo else "\n\n(El video supera 50 MB o no esta local: revisalo en el panel.)"
        r = http(f"{TELEGRAM_API}/bot{token_bot}/sendMessage",
                 datos={"chat_id": chat_id, "text": _cortar(cabecera + nota, LIMITE_MENSAJE),
                        "reply_markup": json.dumps(_teclado(post["token"], url_panel))})

    if not r.get("ok"):
        raise ErrorNotificacion(f"Telegram rechazo el envio: {r.get('description')}")
    with db.sesion() as c:
        db.log(c, "aviso_enviado", {"post_id": post["post_id"], "canal": "telegram"})
    return r.get("result", {})


# ---------------------------------------------------------------------------
# Receptor de botones
#
# Sin esto los botones del mensaje NO HACEN NADA: Telegram entrega los toques
# como callback_query y hay que ir a buscarlos con getUpdates. No hay push.
# ---------------------------------------------------------------------------

def _tg(token_bot: str, metodo: str, datos: dict | None = None, http=_http, timeout: int = 70):
    """Llamada generica a la Bot API."""
    return http(f"{TELEGRAM_API}/bot{token_bot}/{metodo}", datos=datos or {}, timeout=timeout)


def escuchar_telegram(cfg: dict, max_ciclos: int | None = None, http=_http,
                      espera_seg: int = 25) -> list[dict]:
    """
    Long-polling de getUpdates: recibe los toques de los botones y los aplica.

    Devuelve la lista de decisiones procesadas. `max_ciclos` acota la ejecucion
    (util para probar); sin el corre hasta que lo detengas.

    El offset se guarda en la base: si el proceso se reinicia no vuelve a aplicar
    una decision que ya aplicaste. Eso importa porque reprocesar un 'aprobar'
    volveria a meter el post en la cola.
    """
    tg = (cfg.get("notificaciones") or {}).get("telegram") or {}
    from .secrets import obtener
    token_bot = tg.get("bot_token") or obtener("telegram", "bot_token")
    if not token_bot:
        raise ErrorNotificacion(
            "Falta el token del bot. Guardalo con: "
            "`main.py secretos-set --proveedor telegram --campo bot_token`."
        )

    with db.sesion() as c:
        reg = db.uno(c, "SELECT valor FROM bot_state WHERE clave='tg_offset'")
    offset = int(reg["valor"]) if reg and reg["valor"] else 0

    hechos = []
    ciclos = 0
    while max_ciclos is None or ciclos < max_ciclos:
        ciclos += 1
        try:
            r = _tg(token_bot, "getUpdates",
                    {"offset": offset, "timeout": espera_seg,
                     "allowed_updates": json.dumps(["callback_query", "message"])},
                    http=http, timeout=espera_seg + 15)
        except ErrorNotificacion as e:
            hechos.append({"error": str(e)})
            break
        if not r.get("ok"):
            hechos.append({"error": r.get("description", "getUpdates fallo")})
            break

        for up in r.get("result", []):
            offset = max(offset, up["update_id"] + 1)
            cb = up.get("callback_query")
            if not cb:
                continue
            data = cb.get("data") or ""
            accion, _, token = data.partition(":")
            # Telegram exige contestar el callback o el boton se queda "cargando"
            # en el telefono del usuario para siempre.
            try:
                if accion == "ok":
                    res = autorizar_por_token(token, True)
                    texto = f"✅ Aprobado: {res['post_id']}"
                elif accion == "no":
                    res = autorizar_por_token(token, False)
                    texto = f"❌ Rechazado: {res['post_id']}"
                else:
                    texto = "Accion desconocida."
                    res = None
            except ErrorNotificacion as e:
                texto = f"⚠ {e}"
                res = None

            _tg(token_bot, "answerCallbackQuery",
                {"callback_query_id": cb["id"], "text": texto[:200]}, http=http)
            # Reescribir el mensaje deja constancia visible de la decision.
            if cb.get("message", {}).get("message_id"):
                _tg(token_bot, "editMessageText",
                    {"chat_id": cb["message"]["chat"]["id"],
                     "message_id": cb["message"]["message_id"],
                     "text": _cortar(f"{texto}\n\n(decidido desde Telegram)", LIMITE_MENSAJE)},
                    http=http)
            hechos.append({"callback": data, "resultado": res, "texto": texto})

        with db.sesion() as c:
            c.execute("INSERT INTO bot_state (clave, valor) VALUES ('tg_offset', ?) "
                      "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (str(offset),))
    return hechos


def verificar_telegram(cfg: dict | None = None, http=_http) -> dict:
    """
    Comprueba el token y descubre el chat_id solo.

    El chat_id es lo que mas cuesta conseguir a mano (hay que hablarle al bot y
    leer JSON crudo de getUpdates). Aqui se hace automatico: se le pide a
    Telegram la lista de conversaciones recientes y se toma la ultima.
    """
    from .secrets import obtener
    tg = ((cfg or {}).get("notificaciones") or {}).get("telegram") or {}
    token_bot = tg.get("bot_token") or obtener("telegram", "bot_token")
    if not token_bot:
        raise ErrorNotificacion(
            "Falta el token. Crealo con @BotFather (/newbot) y guardalo con: "
            "`main.py secretos-set --proveedor telegram --campo bot_token`."
        )

    yo = _tg(token_bot, "getMe", http=http)
    if not yo.get("ok"):
        raise ErrorNotificacion(f"Telegram rechazo el token: {yo.get('description')}")
    bot = yo["result"]

    ups = _tg(token_bot, "getUpdates", {"timeout": 0}, http=http)
    chats = {}
    for up in ups.get("result", []):
        msg = up.get("message") or up.get("channel_post") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None and chat.get("type") in ("private", "group", "supergroup"):
            chats[cid] = chat.get("title") or chat.get("first_name") or str(cid)
    return {"bot": bot, "chats": chats,
            "chat_id": tg.get("chat_id") or obtener("telegram", "chat_id")}


def enviar_prueba(cfg: dict | None = None, http=_http) -> dict:
    """Manda un mensaje de prueba con botones, para confirmar que todo llega."""
    from .secrets import obtener
    tg = ((cfg or {}).get("notificaciones") or {}).get("telegram") or {}
    token_bot = tg.get("bot_token") or obtener("telegram", "bot_token")
    chat_id = tg.get("chat_id") or obtener("telegram", "chat_id")
    if not (token_bot and chat_id):
        raise ErrorNotificacion(
            "Necesito el token del bot y tu chat_id. Corre `main.py telegram-setup`."
        )
    texto = ("✅ Clipping OS conectado.\n\n"
             "Cuando un clip este listo te llegara aqui con el video, los titulos "
             "propuestos, la descripcion y dos botones.\n\n"
             "Este boton de prueba no toca nada:")
    return _tg(token_bot, "sendMessage",
               {"chat_id": chat_id, "text": texto,
                "reply_markup": json.dumps({"inline_keyboard": [[
                    {"text": "👍 Recibi el mensaje", "callback_data": "prueba:ok"}]]})},
               http=http)


def avisar_webhook(post: dict, cfg: dict, http=None) -> dict:
    """Canal generico: Slack, Discord, ntfy o lo que acepte un POST JSON."""
    url = ((cfg.get("notificaciones") or {}).get("webhook") or {}).get("url")
    if not url:
        raise ErrorNotificacion("Webhook sin URL en config.json (notificaciones.webhook.url).")
    payload = json.dumps({
        "texto": f"Clip listo para {post.get('plataforma')} / {post.get('cuenta')}",
        "text": f"Clip listo para {post.get('plataforma')} / {post.get('cuenta')}",
        "content": f"Clip listo para {post.get('plataforma')} / {post.get('cuenta')}",
        "post_id": post["post_id"], "token": post["token"],
        "copy": post.get("copy"), "archivo": post.get("archivo"),
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        r.read()
    return {"ok": True}


def avisar_todos(cfg: dict, http=_http, url_panel: str = "") -> list[dict]:
    """Avisa todos los pendientes por los canales configurados."""
    canales = (cfg.get("notificaciones") or {}).get("canales") or ["telegram"]
    salida = []
    for p in pendientes():
        for canal in canales:
            try:
                if canal == "telegram":
                    avisar_telegram(p, cfg, http=http, url_panel=url_panel)
                elif canal == "webhook":
                    avisar_webhook(p, cfg)
                else:
                    raise ErrorNotificacion(f"Canal desconocido: {canal}")
                salida.append({"post_id": p["post_id"], "canal": canal, "ok": True})
            except ErrorNotificacion as e:
                salida.append({"post_id": p["post_id"], "canal": canal, "ok": False, "error": str(e)})
    return salida
