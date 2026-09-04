"""Panel web local. Solo stdlib (http.server) para que no haya dependencias que instalar."""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db, discovery, earnings

CSS = """
:root{--bg:#0d1117;--card:#161b22;--line:#21262d;--tx:#e6edf3;--mut:#8b949e;--ok:#3fb950;--warn:#d29922;--bad:#f85149}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);
font:15px/1.55 ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;padding:28px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;color:var(--mut);font-weight:600;
text-transform:uppercase;letter-spacing:.06em;margin:30px 0 10px}
.sub{color:var(--mut);margin:0 0 24px;font-size:14px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
.card .k{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:26px;font-weight:650;margin-top:6px}
.money{color:var(--ok)}table{width:100%;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:14px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#1c2128;color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
tr:last-child td{border-bottom:0}
.pill{padding:2px 9px;border-radius:99px;font-size:12px;font-weight:600}
.p-nueva{background:#1f2d3d;color:#79c0ff}.p-activa{background:#12301c;color:var(--ok)}
.p-bloqueado{background:#3d1418;color:var(--bad)}.p-descartada{background:#2d2d2d;color:var(--mut)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
footer{margin-top:34px;color:var(--mut);font-size:12.5px;border-top:1px solid var(--line);padding-top:14px}
"""


CSS_CRED = """
form{display:grid;gap:8px;max-width:520px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:16px;margin-bottom:14px}
input,select{background:#0d1117;border:1px solid var(--line);color:var(--tx);border-radius:7px;
padding:8px 10px;font:inherit}
button.sub{background:#238636;color:#fff;border:0;border-radius:7px;padding:9px;font-weight:650;cursor:pointer}
.warn{border-left:3px solid var(--warn);padding:10px 12px;background:#1d1600;color:#d29922;
border-radius:6px;font-size:13px;margin-bottom:16px}
code{background:#0d1117;padding:1px 5px;border-radius:4px;font-size:12.5px}
"""


# ---------------------------------------------------------------------------
# Autenticacion del panel
#
# El panel muestra campanas, cuentas y permite aprobar publicaciones. Exponerlo
# a Internet sin clave es regalar el negocio. Si CLIPPER_PANEL_TOKEN esta
# definido, TODO requiere la clave (menos /entrar y /api/salud).
#
# La clave se compara en tiempo constante y se guarda en cookie HttpOnly.
# ---------------------------------------------------------------------------

import hashlib
import hmac
import secrets as _pysecrets

COOKIE = "clipper_clave"


def _token_panel() -> str:
    return os.environ.get("CLIPPER_PANEL_TOKEN", "").strip()


def _firma(clave: str) -> str:
    """Deriva la cookie de la clave: nunca viaja la clave en si."""
    return hmac.new(b"clipping-os-cookie", clave.encode(), hashlib.sha256).hexdigest()[:40]


def _cookies(raw: str) -> dict:
    out = {}
    for pedazo in (raw or "").split(";"):
        if "=" in pedazo:
            k, _, v = pedazo.partition("=")
            out[k.strip()] = v.strip()
    return out


def _autenticado(handler) -> bool:
    tok = _token_panel()
    if not tok:
        return True                      # sin token configurado: acceso libre (local)
    c = _cookies(handler.headers.get("Cookie", ""))
    esperado = _firma(tok)
    recibido = c.get(COOKIE, "")
    return bool(recibido) and hmac.compare_digest(recibido, esperado)


def _pagina_entrar(mensaje: str = "") -> bytes:
    aviso = f"<div class='warn'>{mensaje}</div>" if mensaje else ""
    html = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Entrar — Clipping OS</title><style>{CSS}</style>
<div style="max-width:380px;margin:12vh auto">
<h1>Clipping OS</h1><p class="sub">Acceso restringido</p>
{aviso}
<form method="POST" action="/entrar">
  <input name="clave" type="password" placeholder="Clave de acceso" autofocus
    style="width:100%;padding:11px;border-radius:8px;border:1px solid #30363d;
    background:#0d1117;color:#e6edf3;font-size:15px">
  <button style="width:100%;margin-top:10px;padding:11px;border-radius:8px;border:0;
    background:#238636;color:#fff;font-weight:650;font-size:15px;cursor:pointer">Entrar</button>
</form>
<p style="color:var(--mut);font-size:12px;margin-top:18px">
La clave se define con la variable de entorno <code>CLIPPER_PANEL_TOKEN</code>.
Sin ella el panel solo deberia correr en <code>localhost</code>.</p>
</div></html>"""
    return html.encode()


def _html_kit(kit: dict, token: str = "") -> str:
    """Bloque HTML del kit de publicacion, junto al video."""
    h = lambda t: (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    titulos = ""
    for i, t in enumerate(kit["titulos_propuestos"], 1):
        ev = ("#1f6feb" if t["evidencia"] == "restriccion de plataforma"
              else "#8b949e" if t["evidencia"] == "heuristica" else "#a371f7")
        titulos += (f"<li style='margin:6px 0'><b>{h(t['texto'])}</b> "
                    f"<span style='color:var(--mut)'>({t['caracteres']} car.)</span><br>"
                    f"<span style='color:{ev};font-size:12px'>{h(t['por_que'])}</span></li>")

    hs = kit["hashtags"]
    riesgos = ""
    if kit["riesgos_detectados"]:
        items = "".join(f"<li>'{h(r['frase'])}': {h(r['razon'])}</li>"
                        for r in kit["riesgos_detectados"])
        riesgos = (f"<div style='margin-top:10px;padding:8px 10px;border-radius:7px;"
                   f"background:#3d2a14;border-left:3px solid #d29922'>"
                   f"<b style='color:#d29922'>⚠ Riesgos en el texto</b><ul style='margin:4px 0 0 18px;"
                   f"font-size:12.5px'>{items}</ul></div>")

    ventanas = "".join(f"<li><b>{h(v['ventana'])}</b> — {h(v['por_que'])}</li>"
                       for v in kit["ventanas_publicacion"])
    checks = "".join(f"<li style='{'font-weight:600' if c['obligatorio'] else ''}'>"
                     f"{'☑' if c['obligatorio'] else '☐'} {h(c['item'])}</li>"
                     for c in kit["checklist"])

    return f"""
<div style='margin-top:10px;font-size:13.5px'>
  <div style='color:var(--mut);font-size:12px;letter-spacing:.04em;text-transform:uppercase'>
    Titulos propuestos</div>
  <ol style='margin:4px 0 0 18px;padding:0'>{titulos}</ol>

  <div style='color:var(--mut);font-size:12px;letter-spacing:.04em;text-transform:uppercase;
    margin-top:12px'>Descripcion
    <span style='text-transform:none'>({kit['descripcion_caracteres']}/{kit['descripcion_limite']} car.)</span></div>
  <textarea readonly rows="5" style='width:100%;margin-top:4px;background:#0d1117;color:#e6edf3;
    border:1px solid #30363d;border-radius:7px;padding:8px;font-family:ui-monospace,monospace;
    font-size:12.5px'>{h(kit['descripcion'])}</textarea>

  <div style='margin-top:10px'><b>Hashtags</b>
    <span style='color:var(--mut)'>(max recomendado {kit['hashtags_max_recomendado']})</span><br>
    <span style='color:#1f6feb'>nicho:</span> {h(' '.join(hs['nicho']) or '—')} ·
    <span style='color:#238636'>medio:</span> {h(' '.join(hs['medio']) or '—')} ·
    <span style='color:#8b949e'>amplio:</span> {h(' '.join(hs['amplio']) or '—')}</div>

  <div style='margin-top:10px'><b>Miniatura</b><br>
    <span style='color:var(--mut)'>{h(kit['miniatura']['instruccion'])}</span></div>

  <div style='margin-top:10px'><b>Primer comentario</b><br>
    <span style='color:var(--mut)'>{h(kit['primer_comentario']['texto'])}</span></div>

  <div style='margin-top:10px'><b>Ventanas de publicacion</b> (hora local)
    <ul style='margin:4px 0 0 18px;font-size:12.5px;color:var(--mut)'>{ventanas}</ul></div>

  <div style='margin-top:10px'><b>Checklist antes de publicar</b>
    <ul style='margin:4px 0 0 18px;font-size:12.5px;list-style:none'>{checks}</ul></div>

  {riesgos}

  <div style='margin-top:10px;font-size:11.5px;color:#6e7681;border-top:1px solid #21262d;
    padding-top:8px'>{h(kit['_como_leer_esto'])}</div>
</div>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silenciar el log por defecto
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):  # noqa: N802
        try:
            if self.path == "/entrar":
                largo = int(self.headers.get("Content-Length") or 0)
                cuerpo = urllib.parse.parse_qs(self.rfile.read(largo).decode("utf-8", "replace"))
                dada = (cuerpo.get("clave") or [""])[0]
                tok = _token_panel()
                if tok and hmac.compare_digest(dada, tok):
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie",
                                     f"{COOKIE}={_firma(tok)}; HttpOnly; SameSite=Strict; "
                                     f"Path=/; Max-Age=604800")
                    self.end_headers()
                    return
                b = _pagina_entrar("Clave incorrecta.")
                self.send_response(401)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            if not _autenticado(self):
                return self._json({"error": "no autenticado"}, 401)
            if self.path == "/api/autorizar":
                largo = int(self.headers.get("Content-Length") or 0)
                datos = json.loads(self.rfile.read(largo) or b"{}")
                from . import notify
                try:
                    r = notify.autorizar_por_token(str(datos.get("token", "")),
                                                   bool(datos.get("aprobar")))
                except notify.ErrorNotificacion as e:
                    return self._json({"error": str(e)}, 403)
                with db.sesion() as c:
                    db.log(c, "autorizacion_web", r)
                return self._json(r)
            if self.path != "/api/secretos":
                return self._json({"error": "ruta no encontrada"}, 404)
            largo = int(self.headers.get("Content-Length") or 0)
            if largo > 8192:
                return self._json({"error": "cuerpo demasiado grande"}, 413)
            datos = json.loads(self.rfile.read(largo) or b"{}")
            prov = str(datos.get("proveedor", "")).strip().lower()
            campo = str(datos.get("campo", "api_key")).strip().lower()
            valor = str(datos.get("valor", "")).strip()
            if not (prov and valor):
                return self._json({"error": "faltan 'proveedor' o 'valor'"}, 400)
            from . import secrets
            nivel = secrets.guardar(prov, campo, valor, None)
            with db.sesion() as c:
                db.log(c, "secreto_guardado_web", {"proveedor": prov, "campo": campo, "nivel": nivel})
            # Nunca se devuelve el valor. Ni enmascarado: solo el hecho de que quedo.
            return self._json({"ok": True, "proveedor": prov, "campo": campo, "nivel": nivel})
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/") == "/entrar" and not _autenticado(self):
            b = _pagina_entrar()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if self.path.rstrip("/") == "/api/salud":
            return self._json({"ok": True})
        if not _autenticado(self):
            self.send_response(303)
            self.send_header("Location", "/entrar")
            self.end_headers()
            return
        try:
            self._ruta()
        except Exception as e:  # noqa: BLE001
            # Sin esto el servidor cerraba la conexion en silencio y el cliente
            # veia "RemoteDisconnected" sin ninguna pista del error real.
            import traceback
            self._json({"error": str(e), "trace": traceback.format_exc()}, 500)

    def _ruta(self):
        if self.path == "/api/reporte":
            return self._json(earnings.reporte())
        if self.path == "/api/campanas":
            with db.sesion() as c:
                return self._json(db.filas(c.execute(
                    "SELECT * FROM campaigns ORDER BY puntaje DESC LIMIT 100")))
        if self.path == "/api/cola":
            from . import publish
            return self._json(publish.posts_en_cola())
        if self.path == "/api/secretos":
            from . import secrets
            # Solo estado y muestra enmascarada. El valor completo no sale nunca.
            return self._json([{k: v for k, v in p.items() if k != "requiere"}
                               for p in secrets.inventario()])
        if self.path == "/":
            return self._html()
        if self.path == "/credenciales":
            return self._html_credenciales()
        if self.path.startswith("/autorizar"):
            return self._html_autorizar()
        self.send_error(404)

    def _html_autorizar(self):
        from urllib.parse import parse_qs, urlparse
        from . import notify
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("token") or [""])[0]
        decision = (qs.get("decidir") or [""])[0]

        mensaje = ""
        if decision and token:
            try:
                r = notify.autorizar_por_token(token, decision == "aprobar")
                mensaje = (f"<div class='warn' style='border-left-color:var(--ok);"
                           f"background:#12301c;color:var(--ok)'>"
                           f"{'✅ Aprobado' if decision == 'aprobar' else '❌ Rechazado'}: "
                           f"{r['post_id']}</div>")
            except notify.ErrorNotificacion as e:
                mensaje = f"<div class='warn'>{e}</div>"

        filas = ""
        for p in notify.pendientes():
            video = (f"<video src='/{p['archivo']}' controls style='width:220px;border-radius:8px'></video>"
                     if p.get("archivo") else "<span style='color:var(--mut)'>sin video</span>")
            kit = notify.kit_de(p)
            filas += (f"<div class='card' style='display:flex;gap:16px;margin-bottom:14px;"
                      f"align-items:flex-start'><div>{video}</div>"
                      f"<div style='flex:1'>"
                      f"<div class='k'>{p['plataforma']} · {p['cuenta'] or 'sin cuenta'}"
                      f" · {kit['duracion_seg']}s</div>"
                      + _html_kit(kit, p["token"])
                      + f"<div style='margin-top:12px;display:flex;gap:8px'>"
                      f"<a href='/autorizar?token={p['token']}&decidir=aprobar' "
                      f"style='background:#238636;color:#fff;padding:8px 14px;border-radius:7px;"
                      f"text-decoration:none;font-weight:650'>Aprobar</a>"
                      f"<a href='/autorizar?token={p['token']}&decidir=rechazar' "
                      f"style='background:#3d1418;color:#f85149;padding:8px 14px;border-radius:7px;"
                      f"text-decoration:none;font-weight:650'>Rechazar</a></div></div></div>")
        filas = filas or "<p style='color:var(--mut)'>No hay nada pendiente de autorizacion.</p>"

        html = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Autorizar — Clipping OS</title><style>{CSS}{CSS_CRED}</style>
<h1>Autorizar publicaciones</h1>
<p class="sub">Nada se publica sin que toques Aprobar aquí o en el mensaje de Telegram.</p>
{mensaje}{filas}
<footer>Clipping OS · el enlace de cada pendiente lleva un token aleatorio de 32 bytes;
sin él la decisión no se registra.</footer></html>"""
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html_credenciales(self):
        from . import secrets
        inv = secrets.inventario()
        tarjetas = ""
        for p in inv:
            filas = "".join(
                f"<tr><td class='mono'>{c['campo']}</td>"
                f"<td>{'✅ guardada' if c['guardado'] else '— falta'}</td>"
                f"<td class='mono'>{c['muestra']}</td><td style='color:var(--mut)'>{c['nivel']}</td></tr>"
                for c in p["credenciales"]) or                 "<tr><td colspan='4' style='color:var(--mut)'>No usa credenciales: modo asistido.</td></tr>"
            tarjetas += (
                f"<div class='card' style='margin-bottom:12px'>"
                f"<div class='k'>{p['proveedor']}</div>"
                f"<div style='margin:8px 0;font-size:13px'><b>Entrar:</b> {p.get('login','-')}</div>"
                f"<div style='font-size:13px;color:var(--mut)'><b>La key está en:</b> {p.get('api_key_en','-')}</div>"
                f"<table style='margin-top:10px'>{filas}</table></div>")

        html = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Credenciales — Clipping OS</title><style>{CSS}{CSS_CRED}</style>
<h1>Credenciales</h1>
<p class="sub">Se guardan una sola vez y quedan disponibles para el ciclo automático.</p>
<div class="warn"><b>Esto guarda API keys, no contraseñas.</b> Una API key se revoca en un
clic desde el panel de la herramienta y no da acceso a tu correo ni a tu cuenta completa.
Automatizar el login con usuario y contraseña viola los términos de estas plataformas y
es el patrón que usan para suspender cuentas. Esta página <b>nunca</b> devuelve el valor
guardado: solo si existe y sus primeros caracteres.</div>
<form method="post" action="/api/secretos">
  <label>Proveedor</label>
  <select name="proveedor">
    <option value="opusclip">opusclip</option><option value="tiktok">tiktok</option>
    <option value="instagram">instagram</option><option value="llm">llm</option>
  </select>
  <label>Campo</label>
  <input name="campo" value="api_key">
  <label>Valor (no se vuelve a mostrar)</label>
  <input name="valor" type="password" autocomplete="off" required>
  <button class="sub" type="submit">Guardar</button>
</form>
{tarjetas}
<footer>Clipping OS · las credenciales viven en <code>secrets.local.json</code> (chmod 600)
o en <code>secrets.enc</code> si las guardaste con passphrase desde la CLI.</footer></html>"""
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _html(self):
        r = earnings.reporte()
        with db.sesion() as c:
            campanas = db.filas(c.execute(
                "SELECT * FROM campaigns WHERE estado IN ('nueva','vista','aprobada','activa') "
                "ORDER BY puntaje DESC LIMIT 15"))
            clips = db.filas(c.execute(
                "SELECT id, campaign_id, estado, compliance_ok, compliance_motivos "
                "FROM clips ORDER BY creado_en DESC LIMIT 20"))
        # Se marca la que la compuerta bloquearia: verla en la lista sin mas contexto
        # invitaba a operar una campana que no se puede operar.
        from . import compliance
        for _c in campanas:
            _c["_bloqueada"] = not compliance.revisar_campana(_c).ok

        filas_c = ""
        for x in campanas:
            marca = ("<span class='pill p-bloqueado'>bloqueada</span>" if x.get("_bloqueada")
                     else f"<span class='pill p-{x['estado']}'>{x['estado']}</span>")
            filas_c += (
                f"<tr><td class='mono'>{x['id']}</td><td>{(x.get('marca') or '—')}</td>"
                f"<td>${(x.get('cpm_usd') or 0):.2f}</td>"
                f"<td>{(x.get('presupuesto_rest') or 0):,.0f} / {(x.get('presupuesto_total') or 0):,.0f}</td>"
                f"<td><b>{(x.get('puntaje') or 0):.2f}</b></td><td>{marca}</td></tr>")
        filas_c = filas_c or "<tr><td colspan='6'>Sin campanas. Importa un CSV primero.</td></tr>"

        filas_cl = ""
        for x in clips:
            # La columna admite dos formas: el dict completo de compliance
            # ({"ok":..,"bloqueos":[..],"avisos":[..]}) o una lista suelta de motivos.
            # El DEFAULT del esquema es '[]', asi que hay que tolerar ambas.
            try:
                motivos_raw = json.loads(x.get("compliance_motivos") or "[]")
            except (ValueError, TypeError):
                motivos_raw = []
            if isinstance(motivos_raw, dict):
                motivos = motivos_raw.get("bloqueos", [])
            elif isinstance(motivos_raw, list):
                motivos = motivos_raw
            else:
                motivos = []
            estado = "aprobado" if x["compliance_ok"] else ("bloqueado" if motivos else x["estado"])
            color = "var(--ok)" if x["compliance_ok"] else "var(--bad)"
            filas_cl += (f"<tr><td class='mono'>{x['id']}</td><td class='mono'>{x['campaign_id']}</td>"
                         f"<td style='color:{color}'>{estado}</td>"
                         f"<td style='color:var(--mut)'>{' · '.join(motivos) or '—'}</td></tr>")
        filas_cl = filas_cl or "<tr><td colspan='4'>Sin clips registrados.</td></tr>"

        filas_p = "".join(
            f"<tr><td class='mono'>{d['post']}</td><td>{d['plataforma']}</td><td>{d['views']:,}</td>"
            f"<td>{d['verificadas']:,}</td><td>${d['neto_usd']:.2f}</td>"
            f"<td style='color:var(--mut)'>{d['nota']}</td></tr>"
            for d in r["detalle_posts"]) or "<tr><td colspan='6'>Sin posts aun.</td></tr>"

        html = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clipping OS</title><style>{CSS}</style>
<h1>Clipping OS</h1>
<p class="sub">Panel de rendimiento. Las cifras son del modelo de pago real: vistas
verificadas, umbral minimo, tope por clip, comision de plataforma y presupuesto restante.</p>
<div class="grid">
 <div class="card"><div class="k">Ganancia estimada neta</div><div class="v money">${r['ganancia_estimada_neta_usd']:.2f}</div></div>
 <div class="card"><div class="k">Cobrado realmente</div><div class="v">${r['cobrado_real_usd']:.2f}</div></div>
 <div class="card"><div class="k">Vistas totales</div><div class="v">{r['views_totales']:,}</div></div>
 <div class="card"><div class="k">Posts publicados</div><div class="v">{r['posts']}</div></div>
 <div class="card"><div class="k">Clips bloqueados</div><div class="v">{r['clips_bloqueados_por_compliance']}</div></div>
 <div class="card"><div class="k">Campanas activas</div><div class="v">{r['campanas_rastreadas']}</div></div>
</div>
<h2>Campanas por valor esperado</h2>
<table><tr><th>ID</th><th>Marca</th><th>CPM</th><th>Pool rest / total</th><th>Puntaje</th><th>Estado</th></tr>{filas_c}</table>
<h2>Compuerta de cumplimiento</h2>
<table><tr><th>Clip</th><th>Campana</th><th>Estado</th><th>Motivo del bloqueo</th></tr>{filas_cl}</table>
<h2>Detalle por post</h2>
<table><tr><th>Post</th><th>Red</th><th>Vistas</th><th>Verificadas</th><th>Neto</th><th>Nota</th></tr>{filas_p}</table>
<footer>Clipping OS · nada se publica sin pasar compliance · la publicacion final
siempre requiere tu confirmacion (ver clipper/publish.py)</footer></html>"""
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def servir(host: str = "0.0.0.0", puerto: int = 8000):
    db.inicializar()
    srv = ThreadingHTTPServer((host, puerto), Handler)
    print(f"Panel en http://{host}:{puerto}  (Ctrl+C para salir)")
    srv.serve_forever()
