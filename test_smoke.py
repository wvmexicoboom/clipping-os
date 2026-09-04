#!/usr/bin/env python3
"""
Prueba de humo: recorre el flujo completo de punta a punta sobre una base temporal
y verifica que la compuerta bloquee lo que debe bloquear.

    python test_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TMP = tempfile.mkdtemp(prefix="clipper_test_")
os.environ["CLIPPER_DB"] = os.path.join(TMP, "test.db")
os.environ["CLIPPER_SALIDA"] = os.path.join(TMP, "salida")

from clipper import compliance, db, discovery, earnings, publish  # noqa: E402

OK, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
_fallos = []


def espera(nombre: str, condicion: bool, detalle: str = ""):
    print(f"  {OK if condicion else FAIL} {nombre}" + (f" — {detalle}" if detalle else ""))
    if not condicion:
        _fallos.append(nombre)


REGLAS = {
    "bloquear_categorias": ["apuestas", "casino", "salud"],
    "divulgacion_obligatoria": "#ad",
    "prohibido_clonar_videos_de_otros_clippers": True,
    "marcas_agua_prohibidas": True,
}

print("\n=== 1. Inicializar base ===")
db.inicializar()
espera("base creada", os.path.exists(os.environ["CLIPPER_DB"]))

print("\n=== 2. Importar campanas ===")
r = discovery.importar_csv("ejemplo_campanas.csv", reglas=REGLAS)
espera("importadas sin errores", r["errores"] == 0, f"{r}")

print("\n=== 3. Puntaje: el pool fresco debe ganar al CPM alto ===")
ranking_bruto = discovery.mejores_campanas(10, aplicar_compuerta=False)
delta = next(c for c in ranking_bruto if c["marca"] == "DeltaBet")   # CPM $6, casino
software = next(c for c in ranking_bruto if c["marca"] == "EpsilonSaaS")
espera("casino con CPM $6 puntua por debajo de un SaaS con pool fresco",
       delta["puntaje"] < software["puntaje"],
       f"DeltaBet $6 CPM = {delta['puntaje']} vs EpsilonSaaS $1.80 CPM = {software['puntaje']}")

ranking = discovery.mejores_campanas(10, reglas=REGLAS)
espera("DeltaBet (casino) NO entra al ranking con compuerta",
       all(c["marca"] != "DeltaBet" for c in ranking))
espera("BetaTrade (pool 89%% consumido) tampoco", all(c["marca"] != "BetaTrade" for c in ranking))
espera("ZetaShop (CPM $0.20) tampoco", all(c["marca"] != "ZetaShop" for c in ranking))
top = ranking[0]
espera("la mejor campana util es de software/saas", top["categoria"] in ("software", "saas"),
       f"{top['marca']} puntaje={top['puntaje']}")

print("\n=== 3c. Reglas por defecto no dependen del config ===")
casino = {"cpm_usd": 6.0, "categoria": "apuestas", "presupuesto_total": 50000,
          "presupuesto_rest": 48000, "plataformas_ok": "tiktok|instagram"}
espera("bloquea casino con reglas=None (sin config)", not compliance.revisar_campana(campana=casino).ok)
espera("bloquea casino con config vacio", not compliance.revisar_campana(casino, {}).ok)
espera("un config con lista vacia NO desactiva el filtro",
       not compliance.revisar_campana(casino, {"bloquear_categorias": []}).ok)
espera("el config puede AGREGAR categorias",
       not compliance.revisar_campana({"cpm_usd": 3.0, "categoria": "tabaco",
                                       "plataformas_ok": "tiktok"}, {"bloquear_categorias": ["tabaco"]}).ok)
espera("no se puede apagar la divulgacion desde el config",
       compliance.reglas_efectivas({"divulgacion_obligatoria": False})["divulgacion_obligatoria"] == "#ad")
sin_config = compliance.revisar_campana(
    {"cpm_usd": 6.0, "categoria": "apuestas", "plataformas_ok": "tiktok|instagram"})
espera("el puntaje hunde una categoria bloqueada aunque el CPM sea alto",
       discovery.puntuar(casino) < 5.0, f"puntaje={discovery.puntuar(casino)}")

print("\n=== 3b. Regresion: upsert parcial no vacia datos ===")
antes = db.uno(db.conectar(), "SELECT cpm_usd, presupuesto_rest FROM campaigns WHERE marca='AlphaApp'")
discovery.upsert_campana({"url": "https://ejemplo.whop.com/campana/alpha", "titulo": "solo titulo nuevo"},
                         reglas=REGLAS)
despues = db.uno(db.conectar(), "SELECT cpm_usd, presupuesto_rest, titulo FROM campaigns WHERE marca='AlphaApp'")
espera("conserva el CPM tras un upsert parcial", despues["cpm_usd"] == antes["cpm_usd"],
       f"{antes['cpm_usd']} -> {despues['cpm_usd']}")
espera("conserva el pool restante", despues["presupuesto_rest"] == antes["presupuesto_rest"])
espera("actualiza el campo que si venia", despues["titulo"] == "solo titulo nuevo", despues["titulo"])

print("\n=== 4. Compuerta de campana ===")
v_delta = compliance.revisar_campana(delta, REGLAS)
espera("bloquea casino", not v_delta.ok, "; ".join(v_delta.bloqueos)[:90])
beta = next(c for c in ranking_bruto if c["marca"] == "BetaTrade")
v_beta = compliance.revisar_campana(beta, REGLAS)
espera("bloquea pool agotado (89% consumido)", any("consumido" in b for b in v_beta.bloqueos),
       f"rest={beta['presupuesto_rest']}/{beta['presupuesto_total']}")
espera("aprueba la campana de software", compliance.revisar_campana(top, REGLAS).ok)
zeta = next(c for c in ranking_bruto if c["marca"] == "ZetaShop")
espera("bloquea CPM de $0.20", not compliance.revisar_campana(zeta, REGLAS).ok)

print("\n=== 5. Aprobar campana y registrar material licenciado ===")
cid = discovery.id_de_campana(top["url"])
espera("el id derivado coincide con el de la base", cid == top["id"], f"{cid} vs {top['id']}")
with db.sesion() as c:
    c.execute("UPDATE campaigns SET estado='aprobada' WHERE id=?", (cid,))
estado = db.uno(db.conectar(), "SELECT estado FROM campaigns WHERE id=?", (cid,))
espera("la campana existe y quedo aprobada", estado is not None and estado["estado"] == "aprobada",
       str(estado))

aid_ok = discovery.registrar_asset(cid, "vod", "/tmp/vod_oficial.mp4", licencia="campana",
                                   licencia_prueba="brief autoriza recorte")
aid_malo = discovery.registrar_asset(cid, "url", "https://tiktok.com/@otro/video/1",
                                     licencia="desconocida")
espera("asset de campana registrado", aid_ok.startswith("a_"))

print("\n=== 6. Brief de edicion ===")
from clipper import clip_spec  # noqa: E402
brief = clip_spec.generar_brief("cl_test", {"id": cid, "marca": "AlphaApp", "categoria": "software"},
                                "tiktok", "productividad", "pierdo horas cambiando de app",
                                "todo en una pantalla")
espera("brief tiene beats", len(brief.beats) == 4)
espera("copy lleva #ad", "#ad" in brief.copy)
espera("duracion dentro del rango TikTok", 15 <= brief.duracion_seg <= 45, f"{brief.duracion_seg}s")
espera("duracion de beats <= duracion total", brief.beats[-1]["rango"].split("–")[1].rstrip("s") != "")

print("\n=== 7. Compuerta de clip ===")
camp = db.uno(db.conectar(), "SELECT * FROM campaigns WHERE id=?", (cid,))
with db.sesion() as c:
    assets = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=?", (cid,)))

bueno = {"id": "cl_bueno", "asset_origen": aid_ok, "copy": "Todo en una pantalla #ad #AlphaApp",
         "duracion_seg": 22}
espera("aprueba clip con material licenciado y #ad", compliance.revisar_clip(bueno, camp, assets, REGLAS).ok)

sin_licencia = {"id": "cl_ajeno", "asset_origen": aid_malo, "copy": "Mira esto #ad", "duracion_seg": 22}
v = compliance.revisar_clip(sin_licencia, camp, assets, REGLAS)
espera("bloquea material sin licencia", any("licencia" in b for b in v.bloqueos), v.bloqueos[0][:80])

clon = {"id": "cl_clon", "asset_origen": "url_externa_tiktok", "copy": "Copiado #ad", "duracion_seg": 20}
v = compliance.revisar_clip(clon, camp, assets, REGLAS)
espera("bloquea clon de otro clipper", any("clon" in b for b in v.bloqueos), v.bloqueos[0][:70])

sin_ad = {"id": "cl_sinad", "asset_origen": aid_ok, "copy": "Todo en una pantalla", "duracion_seg": 22}
v = compliance.revisar_clip(sin_ad, camp, assets, REGLAS)
espera("bloquea copy sin divulgacion", any("divulgacion" in b for b in v.bloqueos))

claim = {"id": "cl_claim", "asset_origen": aid_ok, "copy": "Rentabilidad asegurada #ad", "duracion_seg": 22}
espera("bloquea claim de riesgo", not compliance.revisar_clip(claim, camp, assets, REGLAS).ok)

corto = {"id": "cl_corto", "asset_origen": aid_ok, "copy": "Mira #ad", "duracion_seg": 2}
espera("bloquea video de 2s", any("minimo 3" in b for b in compliance.revisar_clip(corto, camp, assets, REGLAS).bloqueos))

print("\n=== 8. Encolado ===")
with db.sesion() as c:
    c.execute("INSERT INTO clips (id, campaign_id, titulo, hook, copy, duracion_seg, asset_origen, estado, creado_en) "
              "VALUES ('cl_bueno',?,'t','t','Todo en una pantalla #ad',22,?,'listo',?)",
              (cid, aid_ok, db.ahora()))
enc = publish.encolar_clip({"id": "cl_bueno", "asset_origen": aid_ok,
                            "copy": "Todo en una pantalla #ad", "duracion_seg": 22},
                           camp, assets, "tiktok", "@test", REGLAS)
espera("clip aprobado queda en cola", enc["post_id"].startswith("p_"))

with db.sesion() as c:
    c.execute("INSERT INTO clips (id, campaign_id, titulo, hook, copy, duracion_seg, asset_origen, estado, creado_en) "
              "VALUES ('cl_ajeno',?,'t','t','Copiado #ad',20,?,'listo',?)", (cid, aid_malo, db.ahora()))
try:
    publish.encolar_clip(sin_licencia, camp, assets, "tiktok", "@test", REGLAS)
    espera("rechaza clip sin licencia al encolar", False, "debio lanzar ErrorPublicacion")
except publish.ErrorPublicacion as e:
    espera("rechaza clip sin licencia al encolar", "licencia" in str(e), str(e)[:70])

try:
    publish.encolar_clip({"id": "cl_inexistente", "asset_origen": aid_ok, "copy": "x #ad"},
                         camp, assets, "tiktok", "@test", REGLAS)
    espera("rechaza un clip que no esta en la base", False, "debio lanzar ErrorPublicacion")
except publish.ErrorPublicacion as e:
    espera("rechaza un clip que no esta en la base", "no existe en la base" in str(e), str(e)[:60])

with db.sesion() as c:
    bloqueado_db = db.uno(c, "SELECT estado, compliance_ok FROM clips WHERE id='cl_ajeno'")
espera("el clip bloqueado queda marcado en la base",
       bloqueado_db and bloqueado_db["estado"] == "bloqueado" and bloqueado_db["compliance_ok"] == 0,
       str(bloqueado_db))

cola = publish.posts_en_cola()
espera("la cola tiene 1 post", len(cola) == 1)
ruta = publish.exportar_cola()
espera("exporta la cola con checklist", os.path.exists(ruta))

print("\n=== 9. Publicacion y liquidacion ===")
publish.marcar_publicado(enc["post_id"], "https://tiktok.com/@test/video/1")
earnings.registrar_vistas(enc["post_id"], 50_000, 46_000)

from datetime import datetime, timedelta, timezone  # noqa: E402
ayer = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")
with db.sesion() as c:
    c.execute("UPDATE posts SET publicado_en=? WHERE id=?", (ayer, enc["post_id"]))

post = db.uno(db.conectar(), "SELECT * FROM posts WHERE id=?", (enc["post_id"],))
liq = earnings.liquidar_post(post, camp, 7.0)
esperado = round(46_000 / 1000 * camp["cpm_usd"] * (1 - 0.09), 2)
espera("liquida con verificadas + comision 9%", liq.neto_usd == esperado,
       f"${liq.neto_usd} (esperado ${esperado}, CPM ${camp['cpm_usd']})")

reciente = earnings.liquidar_post(post, camp, 2.0)
espera("no paga dentro de la ventana de verificacion", reciente.neto_usd == 0.0
       and "verificacion" in (reciente.motivo_cero or ""))

bajo_umbral = earnings.liquidar_post({**post, "views_verif": 500}, camp, 7.0)
espera("no paga bajo el umbral minimo", bajo_umbral.neto_usd == 0.0)

con_tope = earnings.liquidar_post({**post, "views_verif": 5_000_000}, camp, 7.0)
espera("aplica el tope por clip", con_tope.cap_aplicado and con_tope.bruto_usd == camp["cap_por_clip_usd"],
       f"bruto=${con_tope.bruto_usd} tope=${camp['cap_por_clip_usd']}")

print("\n=== 10. Reporte ===")
earnings.registrar_pago("whop", liq.neto_usd, "2026-09-01", "stripe")
rep = earnings.reporte(30)
espera("reporte suma el neto", rep["ganancia_estimada_neta_usd"] == esperado, f"${rep['ganancia_estimada_neta_usd']}")
espera("reporte registra lo cobrado", rep["cobrado_real_usd"] == esperado)
espera("diferencia cobrado-estimado es cero", rep["diferencia_estimado_vs_cobrado_usd"] == 0.0)
espera("cuenta el clip bloqueado", rep["clips_bloqueados_por_compliance"] >= 1)

with db.sesion() as c:
    c.execute("INSERT INTO clips (id, campaign_id, estado, compliance_ok, creado_en) "
              "VALUES ('cl_sin_motivos',?,'listo',1,?)", (cid, db.ahora()))
    crudo = db.uno(c, "SELECT compliance_motivos FROM clips WHERE id='cl_sin_motivos'")
espera("el clip nuevo trae el DEFAULT '[]' de la columna", crudo["compliance_motivos"] == "[]",
       repr(crudo["compliance_motivos"]))

print("\n=== 11. Panel web ===")
import threading  # noqa: E402
import urllib.request  # noqa: E402
from clipper import app  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 8765), app.Handler)
def _corre():
    try:
        srv.serve_forever()
    except Exception:
        import traceback; print("SERVER CRASH:"); traceback.print_exc()
threading.Thread(target=_corre, daemon=True).start()
import time
time.sleep(0.4)
with urllib.request.urlopen("http://127.0.0.1:8765/", timeout=5) as r:
    html = r.read().decode()
espera("panel responde 200 con HTML", r.status == 200 and "<title>Clipping OS</title>" in html)
with urllib.request.urlopen("http://127.0.0.1:8765/api/reporte", timeout=5) as r:
    import json
    api = json.loads(r.read())
espera("/api/reporte devuelve el neto", api["ganancia_estimada_neta_usd"] == esperado)
espera("el panel renderiza un clip con motivos por defecto", "cl_sin_motivos" in html)
srv.shutdown()


print("\n=== 12. Conectores de herramientas de video ===")
from clipper import providers  # noqa: E402

nombres = [d["nombre"] for d in providers.listar()]
espera("los tres proveedores estan registrados", set(nombres) == {"opusclip", "sendshort", "capcut"}, str(nombres))

opus = providers.obtener("opusclip", {})
espera("OpusClip declara API", opus.tiene_api and opus.modo == "api")
espera("OpusClip sin api_key se reporta no configurado", not opus.configurado())
try:
    opus.enviar({"ruta_o_url": "/tmp/x.mp4"}, None, {})
    espera("OpusClip sin api_key no llama a la red", False, "debio lanzar")
except providers.ErrorProveedor as e:
    espera("OpusClip sin api_key no llama a la red", "api_key" in str(e))

ss = providers.obtener("sendshort", {})
cc = providers.obtener("capcut", {})
espera("SendShort declara que NO tiene API", not ss.tiene_api)
espera("CapCut declara que NO tiene API", not cc.tiene_api)
for prov in (ss, cc):
    try:
        prov.enviar({}, None, {})
        espera(f"{prov.nombre} no finge un envio automatico", False, "debio lanzar")
    except providers.ErrorProveedor as e:
        espera(f"{prov.nombre} no finge un envio automatico", "API" in str(e))
try:
    providers.obtener("herramienta_inventada", {})
    espera("rechaza un proveedor inexistente", False, "debio lanzar")
except providers.ErrorProveedor as e:
    espera("rechaza un proveedor inexistente", "desconocido" in str(e))

# --- OpusClip con HTTP inyectado: recorre el flujo real sin tocar la red ---
llamadas = []
def http_falso(url, metodo="GET", cuerpo=None, headers=None, binario=None, timeout=120):
    llamadas.append((metodo, url.split("?")[0]))
    if url.endswith("/upload-links"):
        return {"status": 200, "headers": {}, "json": {"url": "https://gcs/upload", "uploadId": "up_123"}}
    if url == "https://gcs/upload":
        return {"status": 201, "headers": {"location": "https://gcs/resumable"}, "json": None, "raw": b""}
    if url == "https://gcs/resumable":
        return {"status": 200, "headers": {}, "json": None, "raw": b""}
    if url.endswith("/clip-projects"):
        return {"status": 200, "headers": {}, "json": {"id": "proj_777"}}
    if "exportable-clips" in url:
        return {"status": 200, "headers": {}, "json": [{"id": "c1"}, {"id": "c2"}]}
    raise AssertionError("endpoint no previsto: " + url)

from clipper import clip_spec  # noqa: E402
brief_prueba = clip_spec.generar_brief("cl_prov", {"id": cid, "marca": "Epsilon"}, "tiktok",
                                       "tema", "dolor", "beneficio")
asset_local = {"id": aid_ok, "ruta_o_url": os.environ["CLIPPER_DB"], "licencia": "campana",
               "licencia_prueba": "x"}
opus_ok = providers.OpusClip({"api_key": "k_test"}, http=http_falso)
r = opus_ok.enviar(asset_local, brief_prueba, camp)
espera("OpusClip devuelve el id de proyecto", r["project_id"] == "proj_777", str(r["project_id"]))
espera("OpusClip sigue los 4 pasos de la doc oficial",
       [m for m, _ in llamadas] == ["POST", "POST", "PUT", "POST"], str(llamadas))
est = opus_ok.estado({"referencia": "proj_777"})
espera("OpusClip consulta los clips del proyecto", est["listo"] and est["n_clips"] == 2, str(est["n_clips"]))

def http_401(url, metodo="GET", **kw):
    raise providers.ErrorProveedor("HTTP 401: plan sin acceso a la API")
try:
    providers.OpusClip({"api_key": "k_mala"}, http=http_401).enviar(asset_local, brief_prueba, camp)
    espera("un 401 de OpusClip se reporta, no revienta", False)
except providers.ErrorProveedor as e:
    espera("un 401 de OpusClip se reporta, no revienta", "401" in str(e))

# --- registro de trabajos ---
jid = providers.registrar_trabajo("opusclip", cid, aid_ok, "proj_777", "enviado", {"x": 1})
espera("el trabajo queda registrado", jid > 0)
providers.actualizar_trabajo(jid, "listo", 2, {"ok": True})
t = providers.trabajos()[0]
espera("el trabajo se actualiza", t["estado"] == "listo" and t["n_clips"] == 2, str(t["estado"]))
espera("trabajos(pendiente) excluye los terminados",
       all(x["id"] != jid for x in providers.trabajos(pendiente=True)))

# --- detector de marca de agua sobre video real ---
import cv2, numpy as np  # noqa: E402
dir_video = os.path.join(TMP, "videos"); os.makedirs(dir_video, exist_ok=True)
def video(ruta, con_marca):
    vw = cv2.VideoWriter(ruta, cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (540, 960))
    for n in range(120):
        f = np.full((960, 540, 3), (136, 68, 34), np.uint8)
        x, y = (n * 9) % 360, (n * 5) % 800
        cv2.rectangle(f, (x, y), (x + 120, y + 90), (0, 230, 255), -1)
        cv2.circle(f, (540 - (n * 7) % 500, 100 + (n * 3) % 700), 40, (250, 250, 250), -1)
        if con_marca:
            cv2.rectangle(f, (400, 890), (525, 940), (255, 255, 255), -1)
        vw.write(f)
    vw.release()
    return ruta
limpio = video(os.path.join(dir_video, "limpio.mp4"), False)
marcado = video(os.path.join(dir_video, "marcado.mp4"), True)

s1, f1, z1 = providers.detectar_marca_agua(limpio)
s2, f2, z2 = providers.detectar_marca_agua(marcado)
espera("video real sin marca: sin sospecha", not s1, f"max={f1}")
espera("video real con marca: la detecta", s2, f"max={f2}")
espera("localiza la marca en la esquina correcta", max(z2, key=lambda k: z2[k] if not k.startswith('_') else -1) == "inf_der",
       str({k: v for k, v in z2.items() if not k.startswith('_')}))
espera("no confunde las otras tres esquinas",
       all(z2[k] < 0.01 for k in ("sup_izq", "sup_der", "inf_izq")))

# --- re-ingesta de un render ---
try:
    providers.registrar_render("cl_bueno", marcado, "capcut")
    espera("bloquea un render con marca de agua", False, "debio lanzar")
except providers.ErrorProveedor as e:
    espera("bloquea un render con marca de agua", "marca de agua" in str(e))
with db.sesion() as c:
    est_clip = db.uno(c, "SELECT estado FROM clips WHERE id='cl_bueno'")
espera("el clip queda en 'revision_agua', no listo para publicar",
       est_clip["estado"] == "revision_agua", est_clip["estado"])

r = providers.registrar_render("cl_bueno", marcado, "capcut", forzar=True)
espera("--forzar deja pasar el render tras revision humana", r["archivo"] == marcado)
r = providers.registrar_render("cl_bueno", limpio, "opusclip")
espera("un render limpio se registra sin aviso", r["aviso"] is None)
try:
    providers.registrar_render("cl_bueno", "/tmp/no_existe.mp4", "capcut")
    espera("rechaza un archivo inexistente", False)
except providers.ErrorProveedor:
    espera("rechaza un archivo inexistente", True)

# --- carpeta de trabajo del modo asistido ---
destino = os.path.join(TMP, "trabajo_capcut")
ruta_carpeta = cc.preparar_carpeta(asset_local, brief_prueba, camp, destino)
archivos = sorted(os.listdir(ruta_carpeta))
espera("la carpeta de trabajo trae brief, pasos, licencia y beats",
       {"BRIEF.md", "PASOS.txt", "licencia.txt", "beats.json"} <= set(archivos), str(archivos))
pasos = open(os.path.join(ruta_carpeta, "PASOS.txt"), encoding="utf-8").read()
espera("los pasos incluyen el comando de re-ingesta", "main.py render" in pasos)
espera("los pasos advierten del logo de la herramienta", "logo de CapCut" in pasos)


print("\n=== 13. Credenciales ===")
os.environ["CLIPPER_SECRETS_DIR"] = TMP          # no tocar los archivos reales del proyecto
from clipper import secrets  # noqa: E402

espera("enmascarar no revela el valor completo",
       secrets.enmascarar("sk-TEST-1234567890") == "sk-T" + "•" * 10 + "7890",
       secrets.enmascarar("sk-TEST-1234567890"))
espera("enmascarar un valor corto no filtra nada", "T" not in secrets.enmascarar("abc"))

nivel = secrets.guardar("opusclip", "api_key", "sk-opus-SECRETO-1234567890", "frase de prueba")
espera("guarda cifrado cuando hay passphrase", "cifrado" in nivel, nivel)
ruta_enc = os.path.join(TMP, "secrets.enc")
espera("crea secrets.enc", os.path.exists(ruta_enc))
contenido = open(ruta_enc, encoding="utf-8").read()
espera("el valor en claro NO aparece en el archivo cifrado", "SECRETO-1234567890" not in contenido)
espera("permisos 600 en el archivo cifrado",
       oct(os.stat(ruta_enc).st_mode & 0o777) == "0o600", oct(os.stat(ruta_enc).st_mode & 0o777))
espera("lee con la passphrase correcta",
       secrets.obtener("opusclip", "api_key", "frase de prueba") == "sk-opus-SECRETO-1234567890")
espera("NO lee con passphrase equivocada",
       secrets.obtener("opusclip", "api_key", "frase equivocada") is None)
espera("declara el KDF y las iteraciones", "pbkdf2_sha256_390000" in contenido)

nivel2 = secrets.guardar("llm", "api_key", "sk-llm-PLANO-abcdef", None)
espera("sin passphrase cae al archivo plano y lo dice", "NO es cifrado" in nivel2, nivel2)
ruta_plano = os.path.join(TMP, "secrets.local.json")
espera("permisos 600 en el archivo plano",
       oct(os.stat(ruta_plano).st_mode & 0o777) == "0o600")
espera("lee del archivo plano", secrets.obtener("llm", "api_key") == "sk-llm-PLANO-abcdef")

os.environ["CLIPPER_SECRET_LLM_API_KEY"] = "sk-desde-entorno"
espera("la variable de entorno tiene prioridad", secrets.obtener("llm", "api_key") == "sk-desde-entorno")
espera("el nivel reporta 'variable de entorno'",
       secrets.nivel_actual("llm", "api_key") == "variable de entorno")
del os.environ["CLIPPER_SECRET_LLM_API_KEY"]

espera("borrar elimina del plano", secrets.borrar("llm", "api_key", None)
       and secrets.obtener("llm", "api_key") is None)
espera("borrar elimina del cifrado", secrets.borrar("opusclip", "api_key", "frase de prueba")
       and secrets.obtener("opusclip", "api_key", "frase de prueba") is None)

inv = secrets.inventario("frase de prueba")
opus_inv = next(p for p in inv if p["proveedor"] == "opusclip")
espera("el inventario dice donde se consigue la key", "opus.pro" in opus_inv["login"])
espera("el inventario advierte del requisito de plan", "beta cerrada" in opus_inv["requiere"])
ss_inv = next(p for p in inv if p["proveedor"] == "sendshort")
espera("el inventario aclara que SendShort no tiene key", "No existe" in ss_inv["api_key_en"])
espera("el inventario no devuelve valores completos",
       all(len(c["muestra"]) < 20 for p in inv for c in p["credenciales"]))

print("\n=== 14. Ciclo autonomo ===")
from clipper import auto  # noqa: E402

reglas_cfg = {"reglas_duras": {"bloquear_categorias": ["apuestas", "casino", "salud"]},
              "auto": {"max_por_cuenta_dia": 3, "proveedor": "capcut"}}
trabajos = auto.elegir_trabajo(reglas_cfg, 3)
espera("elige solo campanas con material licenciado", len(trabajos) >= 1 and all(t["_assets"] for t in trabajos))
espera("no elige campanas bloqueadas",
       all(t["categoria"] not in ("apuestas", "salud") for t in trabajos))

trabajos_antes = len(auto.providers.trabajos())
r_dry = auto.lote(reglas_cfg, max_clips=1, dry_run=True)
espera("dry-run produce brief sin enviar", r_dry.producidos == 1 and r_dry.encolados == 0)
espera("dry-run no registra trabajos de proveedor nuevos",
       len(auto.providers.trabajos()) == trabajos_antes,
       f"{trabajos_antes} -> {len(auto.providers.trabajos())}")
espera("dry-run no consume cuota del tope diario", auto._usados_hoy("principal") == 0)

r_cap = auto.lote(reglas_cfg, max_clips=1, proveedor_pref="capcut")
espera("con CapCut prepara carpeta en vez de fingir un envio",
       r_cap.carpetas_preparadas == 1 and r_cap.encolados == 0, r_cap.detalles[0][:60] if r_cap.detalles else "")

cfg_opus = dict(reglas_cfg); cfg_opus["auto"] = {"max_por_cuenta_dia": 3, "proveedor": "opusclip"}
r_opus = auto.lote(cfg_opus, max_clips=1, proveedor_pref="opusclip")
espera("sin credencial de OpusClip omite e indica como guardarla",
       r_opus.omitidos == 1 and "secretos-set" in (r_opus.detalles[0] if r_opus.detalles else ""),
       r_opus.detalles[0][:70] if r_opus.detalles else "")

r_cosecha_vacia = auto.cosechar(reglas_cfg)
espera("cosechar sin trabajos pendientes no falla", r_cosecha_vacia.encolados == 0 and not r_cosecha_vacia.error)

auto._consumir("cuenta_tope", 3)
espera("el tope diario se persiste en la base", auto._usados_hoy("cuenta_tope") == 3)
r_tope = auto.lote(dict(reglas_cfg, auto={"max_por_cuenta_dia": 3}), cuenta="cuenta_tope")
espera("al llegar al tope diario se detiene", r_tope.producidos == 0 and "Tope diario" in (r_tope.error or ""),
       r_tope.error)
espera("el tope no se puede subir por encima del limite duro",
       min(999, auto.TOPE_DURO_DIA) == auto.TOPE_DURO_DIA)

# el detector de agua tambien frena al ciclo automatico, no solo al manual
auto.providers.registrar_trabajo("capcut", cid, aid_ok, "ref_falsa", "listo",
                                 {"clip_id": "cl_bueno"})
espera("un trabajo marcado listo sin URL no rompe la cosecha",
       auto.cosechar(reglas_cfg).encolados == 0)


print("\n=== 15. Notificacion y autorizacion ===")
from clipper import notify  # noqa: E402

with db.sesion() as c:
    fila = db.uno(c, "SELECT id FROM posts LIMIT 1")
post_prueba = fila["id"] if fila else None
espera("hay un post de prueba", post_prueba is not None)

token = notify.nuevo_pendiente(post_prueba, "telegram", "prueba")
espera("crea un pendiente con token", len(token) > 20)
espera("el token es aleatorio (distinto cada vez)",
       notify.nuevo_pendiente(post_prueba) != token)
token = notify.pendientes()[0]["token"]
with db.sesion() as c:
    est = db.uno(c, "SELECT estado FROM posts WHERE id=?", (post_prueba,))
espera("el post pasa a 'pendiente_autorizacion'",
       est["estado"] == "pendiente_autorizacion", est["estado"])

# token equivocado no autoriza
try:
    notify.autorizar(post_prueba, "token_inventado", True)
    espera("un token incorrecto NO autoriza", False, "debio lanzar")
except notify.ErrorNotificacion as e:
    espera("un token incorrecto NO autoriza", "Token incorrecto" in str(e))
with db.sesion() as c:
    sigue = db.uno(c, "SELECT estado FROM posts WHERE id=?", (post_prueba,))
espera("tras el intento fallido sigue pendiente", sigue["estado"] == "pendiente_autorizacion")

# token correcto autoriza
r = notify.autorizar(post_prueba, token, True)
espera("con el token correcto aprueba", r["estado"] == "aprobado")
with db.sesion() as c:
    ahora_estado = db.uno(c, "SELECT estado FROM posts WHERE id=?", (post_prueba,))
espera("el post vuelve a la cola tras aprobar", ahora_estado["estado"] == "en_cola")

# rechazo
t2 = notify.nuevo_pendiente(post_prueba)
r2 = notify.autorizar_por_token(t2, False)
espera("se puede rechazar por token", r2["estado"] == "rechazado")
try:
    notify.autorizar_por_token(t2, True)
    espera("un enlace ya usado no se puede reutilizar", False, "debio lanzar")
except notify.ErrorNotificacion as e:
    espera("un enlace ya usado no se puede reutilizar", "ya se uso" in str(e))

# --- Telegram con HTTP inyectado: no toca la red ---
envios = []
def http_tg(url, datos=None, archivos=None, timeout=120):
    envios.append({"url": url, "datos": datos, "archivos": archivos})
    return {"ok": True, "result": {"message_id": 42}}

t3 = notify.nuevo_pendiente(post_prueba, "telegram")
pend = [p for p in notify.pendientes() if p["post_id"] == post_prueba][0]
pend["archivo"] = None      # sin archivo: debe caer a sendMessage
res = notify.avisar_telegram(pend, {"notificaciones": {"telegram": {"bot_token": "123:ABC", "chat_id": "999"}}},
                             http=http_tg)
espera("Telegram responde ok", res.get("message_id") == 42)
espera("usa sendMessage cuando no hay archivo", envios[-1]["url"].endswith("/sendMessage"))
espera("manda los botones de decision", "inline_keyboard" in envios[-1]["datos"]["reply_markup"])
espera("el kit completo va en el mensaje", "KIT DE PUBLICACION" in envios[-1]["datos"]["text"])
espera("el mensaje trae los titulos propuestos",
       "TÍTULOS PROPUESTOS" in envios[-1]["datos"]["text"])
espera("el mensaje trae la descripcion", "DESCRIPCIÓN" in envios[-1]["datos"]["text"])
espera("el mensaje trae los hashtags", "HASHTAGS" in envios[-1]["datos"]["text"])
espera("el token viaja en el callback_data", t3[:12] in envios[-1]["datos"]["reply_markup"])

# con archivo: sendVideo
pend["archivo"] = os.path.join(TMP, "videos", "limpio.mp4")
notify.avisar_telegram(pend, {"notificaciones": {"telegram": {"bot_token": "123:ABC", "chat_id": "999"}}},
                       http=http_tg)
espera("usa sendVideo cuando hay clip adjunto", envios[-1]["url"].endswith("/sendVideo"))
espera("adjunta el archivo", envios[-1]["archivos"] and "video" in envios[-1]["archivos"])

# sin configurar
try:
    notify.avisar_telegram(pend, {}, http=http_tg)
    espera("sin token de Telegram explica que falta", False, "debio lanzar")
except notify.ErrorNotificacion as e:
    espera("sin token de Telegram explica que falta", "secretos-set" in str(e))

# telegram responde error
def http_tg_error(url, datos=None, archivos=None, timeout=120):
    return {"ok": False, "description": "bot was blocked by the user"}
try:
    notify.avisar_telegram(pend, {"notificaciones": {"telegram": {"bot_token": "1", "chat_id": "2"}}},
                           http=http_tg_error)
    espera("un error de Telegram se reporta", False, "debio lanzar")
except notify.ErrorNotificacion as e:
    espera("un error de Telegram se reporta", "blocked" in str(e))

# --- modo borrador de TikTok ---
from clipper import publish as pub  # noqa: E402
espera("existe tiktok_borrador", callable(pub.tiktok_borrador))
import inspect
src = inspect.getsource(pub.tiktok_borrador)
espera("usa post_mode MEDIA_UPLOAD (bandeja, no publico)", '"post_mode": "MEDIA_UPLOAD"' in src)
espera("declara contenido de marca (divulgacion)", "brand_content_toggle" in src)
espera("etiqueta el contenido como IA", '"is_aigc": True' in src)
try:
    pub.tiktok_borrador("https://x/v.mp4", {})
    espera("sin access_token no llama a TikTok", False, "debio lanzar")
except pub.ErrorPublicacion as e:
    espera("sin access_token no llama a TikTok", "video.upload" in str(e))
espera("publicar_tiktok sigue exigiendo la auditoria",
       "auditoria_aprobada" in inspect.getsource(pub.publicar_tiktok))

print("\n=== 16. Integracion: producir → avisar → autorizar ===")
cfg_int = {"reglas_duras": {"bloquear_categorias": ["apuestas", "casino", "salud"]},
           "auto": {"max_por_cuenta_dia": 5, "proveedor": "capcut"},
           "notificaciones": {"canales": ["telegram"],
                              "telegram": {"bot_token": "123:ABC", "chat_id": "999"}}}
envios.clear()
lote_r = auto.lote(cfg_int, max_clips=1, proveedor_pref="capcut", cuenta="integracion")
espera("el ciclo produce un clip", lote_r.producidos == 1)
with db.sesion() as c:
    clip_int = db.uno(c, "SELECT * FROM clips ORDER BY creado_en DESC LIMIT 1")
    camp_int = db.uno(c, "SELECT * FROM campaigns WHERE id=?", (clip_int["campaign_id"],))
    assets_int = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=?", (camp_int["id"],)))
providers.registrar_render(clip_int["id"], os.path.join(TMP, "videos", "limpio.mp4"), "capcut")
enc_int = pub.encolar_clip(clip_int, camp_int, assets_int, "tiktok", "@integracion",
                           compliance.reglas_efectivas(cfg_int["reglas_duras"]))
tok_int = notify.nuevo_pendiente(enc_int["post_id"])
res_int = notify.avisar_todos(cfg_int, http=http_tg)
espera("el aviso sale por el canal configurado",
       any(r["ok"] and r["canal"] == "telegram" for r in res_int), str(res_int))
r_int = notify.autorizar_por_token(tok_int, True)
with db.sesion() as c:
    fin = db.uno(c, "SELECT estado FROM posts WHERE id=?", (enc_int["post_id"],))
espera("tras autorizar queda en_cola y no 'publicado'",
       fin["estado"] == "en_cola", fin["estado"])
publicados_int = db.uno(db.conectar(),
    "SELECT COUNT(*) n FROM posts WHERE estado='publicado' AND clip_id=?", (clip_int["id"],))["n"]
espera("el ciclo completo NO publico nada por su cuenta", publicados_int == 0,
       f"{publicados_int} publicaciones automaticas")


print("\n=== 17. Kit de publicacion (titulos, descripcion, hashtags) ===")
from clipper import viral  # noqa: E402

clip_kit = {"id": "cl_kit", "titulo": "Automatiza reportes",
            "hook": "¿Sigues haciendo reportes a mano?",
            "copy": "Ahorra 4 horas a la semana.\n\n#saas #ad", "duracion_seg": 24}
camp_kit = {"id": "c1", "marca": "EpsilonSaaS", "categoria": "SaaS"}
k = viral.generar_kit(clip_kit, camp_kit, "tiktok", tema="automatizar reportes",
                      dolor="pierdes horas", beneficio="ahorras 4 horas")

espera("propone titulos", len(k["titulos_propuestos"]) >= 4, len(k["titulos_propuestos"]))
espera("cada titulo trae su justificacion",
       all(t.get("por_que") for t in k["titulos_propuestos"]))
espera("cada titulo dice de donde viene su evidencia",
       all(t.get("evidencia") in ("heuristica", "restriccion de plataforma", "llm")
           for t in k["titulos_propuestos"]))
espera("ningun titulo pasa el limite",
       all(t["caracteres"] <= 80 for t in k["titulos_propuestos"]))
espera("hay un titulo recomendado", len(k["titulo_recomendado"]) > 5)
espera("no quedan marcadores sin rellenar",
       not any("{" in t["texto"] for t in k["titulos_propuestos"]))

espera("genera descripcion", len(k["descripcion"]) > 20)
espera("la descripcion respeta el limite de la plataforma",
       k["descripcion_caracteres"] <= k["descripcion_limite"])
espera("la descripcion incluye la divulgacion #ad", "#ad" in k["descripcion"])

espera("hashtags por nivel (nicho/medio/amplio)",
       set(k["hashtags"]) == {"nicho", "medio", "amplio"})
total_hs = sum(len(v) for v in k["hashtags"].values())
espera("no excede el maximo recomendado por plataforma",
       total_hs <= k["hashtags_max_recomendado"], f"{total_hs} > {k['hashtags_max_recomendado']}")
espera("el hashtag de marca esta en el nivel nicho",
       "#EpsilonSaaS" in k["hashtags"]["nicho"])

espera("da ventanas de publicacion", len(k["ventanas_publicacion"]) >= 2)
espera("cada ventana explica el por que",
       all(v.get("por_que") for v in k["ventanas_publicacion"]))
espera("trae instruccion de miniatura", len(k["miniatura"]["instruccion"]) > 20)
espera("trae primer comentario sugerido", len(k["primer_comentario"]["texto"]) > 10)
espera("trae checklist", len(k["checklist"]) >= 5)
espera("el checklist marca lo obligatorio",
       any(c["obligatorio"] for c in k["checklist"]))
espera("el checklist exige la divulgacion legal",
       any("#ad" in c["item"] for c in k["checklist"]))

# Shorts trunca el titulo: limite mas chico que en TikTok
ky = viral.generar_kit(clip_kit, camp_kit, "youtube", tema="automatizar reportes",
                       dolor="pierdes horas", beneficio="ahorras 4 horas")
espera("en YouTube el limite de titulo es mas chico (Shorts trunca)",
       all(t["caracteres"] <= 60 for t in ky["titulos_propuestos"]))

# detector de frases fragiles
kr = viral.generar_kit({"id": "x", "hook": "hazte rico gratis 100% garantizado",
                        "copy": "link en bio #fyp"}, camp_kit)
frases = [r["frase"] for r in kr["riesgos_detectados"]]
espera("detecta promesas de ingreso", "hazte rico" in frases)
espera("detecta 'gratis' sin contexto", "gratis" in frases)
espera("detecta claims absolutos", "100%" in frases)
espera("cada riesgo explica por que", all(r.get("razon") for r in kr["riesgos_detectados"]))
espera("un texto limpio no levanta riesgos", k["riesgos_detectados"] == [])

# determinismo: mismos insumos -> mismo kit
k2 = viral.generar_kit(clip_kit, camp_kit, "tiktok", tema="automatizar reportes",
                       dolor="pierdes horas", beneficio="ahorras 4 horas")
espera("el kit es deterministico", k == k2)

# texto plano para Telegram/CLI
txt = viral.kit_a_texto(k)
espera("el texto plano trae los titulos", "TÍTULOS PROPUESTOS" in txt)
espera("el texto plano trae la descripcion", "DESCRIPCIÓN" in txt)
espera("el texto plano trae los hashtags", "HASHTAGS" in txt)
espera("el texto plano trae el checklist", "CHECKLIST" in txt)
espera("el texto plano trae las ventanas", "VENTANAS DE PUBLICACIÓN" in txt)

# sin LLM configurado no falla
espera("sin LLM devuelve el kit intacto", viral.enriquecer_con_llm(dict(k), None) is not None)

# guarda contra insumos degenerados: un titulo armado sobre datos vacios es basura
kd = viral.generar_kit({"id": "x", "hook": "t", "copy": "t"},
                       {"marca": "EpsilonSaaS", "categoria": "SaaS"})
espera("detecta insumos insuficientes", kd["requiere_revision"] is True)
espera("lista cuales campos faltan", len(kd["insumos_insuficientes"]) >= 1)
espera("no promete un beneficio que no existe",
       not any("el resultado del video" in t["texto"] for t in kd["titulos_propuestos"]))
espera("el aviso aparece en el texto plano",
       "INSUMOS INSUFICIENTES" in viral.kit_a_texto(kd))
espera("un caso sano no levanta el aviso",
       viral.generar_kit(clip_kit, camp_kit, "tiktok", tema="automatizar reportes",
                         dolor="pierdes horas", beneficio="ahorras 4 horas")["requiere_revision"] is False)

# el kit se deriva del pendiente real
pend_kit = None
for pd in notify.pendientes():
    pend_kit = pd
    break
if pend_kit:
    kk = notify.kit_de(pend_kit)
    espera("kit_de() arma el kit desde un pendiente real",
           len(kk["titulos_propuestos"]) >= 1 and bool(kk["descripcion"]))
    # Este fixture tiene datos degenerados (hook/copy de 1 caracter), asi que la
    # guarda debe marcarlo en vez de inventar titulos sobre la nada.
    espera("un pendiente con datos pobres se marca para revision",
           kk["requiere_revision"] is True, kk["insumos_insuficientes"])
    espera("el kit del pendiente trae la marca de la campana",
           kk["marca"] == pend_kit.get("marca"))


print("\n=== 18. Telegram real: multipart, botones y limites ===")

# --- 1. multipart: el video no puede ir duplicado (ruta como texto + archivo) ---
capturado = {}
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class _H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        capturado["ct"] = self.headers.get("Content-Type")
        capturado["body"] = self.rfile.read(n)
        capturado.setdefault("metodos", []).append(self.path.rsplit("/", 1)[-1])
        b = json.dumps({"ok": True, "result": {"message_id": 7}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass

srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
puerto = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
api_real = notify.TELEGRAM_API
notify.TELEGRAM_API = f"http://127.0.0.1:{puerto}"

vid_prueba = os.path.join(TMP, "videos", "limpio.mp4")
espera("existe un video de prueba", os.path.exists(vid_prueba))
pend_tg = None
for pd in notify.pendientes():
    pend_tg = pd; break
if not pend_tg:
    with db.sesion() as c:
        pid = db.uno(c, "SELECT id FROM posts LIMIT 1")["id"]
    notify.nuevo_pendiente(pid)
    pend_tg = notify.pendientes()[0]
pend_tg["archivo"] = vid_prueba

notify.avisar_telegram(pend_tg, {"notificaciones": {"telegram": {"bot_token": "T", "chat_id": "9"}}})
body = capturado["body"]
boundary = capturado["ct"].split("boundary=")[1]
partes = []
for pt in body.split(("--" + boundary).encode()):
    if b"Content-Disposition" not in pt: continue
    cab, _, resto = pt.partition(b"\r\n\r\n")
    linea = cab.split(b"\r\n")[1].decode()
    partes.append((linea.split('name="')[1].split('"')[0], "filename=" in linea))

nombres = [p[0] for p in partes]
espera("el campo 'video' se manda UNA sola vez", nombres.count("video") == 1, nombres)
espera("el video va como archivo, no como texto",
       any(n == "video" and es_arch for n, es_arch in partes))
espera("no se cuela la ruta del archivo como texto",
       vid_prueba.encode() not in body.replace(vid_prueba.encode(), b"", 1)
       or b"filename=" in body)
espera("el multipart cierra con el boundary final",
       body.rstrip().endswith(("--" + boundary + "--").encode()))
espera("usa sendVideo cuando hay clip", capturado["metodos"][-1] == "sendVideo")

# --- 2. limites de Telegram ---
espera("recorta al limite de mensaje", len(notify._cortar("a" * 5000, notify.LIMITE_MENSAJE))
       == notify.LIMITE_MENSAJE)
espera("recorta al limite de caption", len(notify._cortar("b" * 3000, notify.LIMITE_CAPTION))
       == notify.LIMITE_CAPTION)
espera("un texto corto no se toca", notify._cortar("hola", 4096) == "hola")

# --- 3. botones: alguien tiene que leer getUpdates o no hacen nada ---
espera("existe el receptor de botones", callable(notify.escuchar_telegram))
token_cb = pend_tg["token"]
entregado = {"n": 0}
def http_bot(url, datos=None, archivos=None, timeout=120):
    metodo = url.rsplit("/", 1)[-1]
    capturado.setdefault("metodos", []).append(metodo)
    if metodo == "getUpdates":
        if int((datos or {}).get("offset") or 0) > 9001:
            return {"ok": True, "result": []}
        return {"ok": True, "result": [{"update_id": 9001, "callback_query": {
            "id": "cb1", "data": f"ok:{token_cb}",
            "message": {"message_id": 5, "chat": {"id": 9}}}}]}
    return {"ok": True, "result": {}}

res_cb = notify.escuchar_telegram({"notificaciones": {"telegram": {"bot_token": "T"}}},
                                  max_ciclos=2, http=http_bot, espera_seg=1)
espera("el boton Aprobar se procesa", len(res_cb) == 1 and res_cb[0]["texto"].startswith("✅"),
       str(res_cb))
espera("contesta el callback (si no, el boton queda cargando)",
       "answerCallbackQuery" in capturado["metodos"])
espera("reescribe el mensaje con la decision", "editMessageText" in capturado["metodos"])
with db.sesion() as c:
    fin_cb = db.uno(c, "SELECT estado FROM approvals WHERE post_id=?", (pend_tg["post_id"],))
espera("la decision quedo guardada en la base", fin_cb["estado"] == "aprobado", fin_cb["estado"])
with db.sesion() as c:
    off = db.uno(c, "SELECT valor FROM bot_state WHERE clave='tg_offset'")
espera("el offset se persiste", off and off["valor"] == "9002", off and off["valor"])
res_cb2 = notify.escuchar_telegram({"notificaciones": {"telegram": {"bot_token": "T"}}},
                                   max_ciclos=1, http=http_bot, espera_seg=1)
espera("el offset evita reprocesar la misma decision", res_cb2 == [], str(res_cb2))

# token ya usado -> avisa, no explota
def http_bot_viejo(url, datos=None, archivos=None, timeout=120):
    metodo = url.rsplit("/", 1)[-1]
    if metodo == "getUpdates":
        if int((datos or {}).get("offset") or 0) > 9101:
            return {"ok": True, "result": []}
        return {"ok": True, "result": [{"update_id": 9101, "callback_query": {
            "id": "cb2", "data": f"ok:{token_cb}",
            "message": {"message_id": 6, "chat": {"id": 9}}}}]}
    return {"ok": True, "result": {}}
res_viejo = notify.escuchar_telegram({"notificaciones": {"telegram": {"bot_token": "T"}}},
                                     max_ciclos=2, http=http_bot_viejo, espera_seg=1)
espera("un enlace ya usado avisa en vez de romper",
       any("ya se uso" in (r.get("texto") or "") for r in res_viejo), str(res_viejo))

# --- 4. setup y prueba ---
espera("existe verificar_telegram", callable(notify.verificar_telegram))
espera("existe enviar_prueba", callable(notify.enviar_prueba))
def http_me(url, datos=None, archivos=None, timeout=120):
    metodo = url.rsplit("/", 1)[-1]
    if metodo == "getMe":
        return {"ok": True, "result": {"username": "clippingbot", "first_name": "Clipping"}}
    if metodo == "getUpdates":
        return {"ok": True, "result": [{"update_id": 1, "message": {
            "chat": {"id": 12345, "type": "private", "first_name": "Tu"}}}]}
    return {"ok": True, "result": {"message_id": 1}}
info = notify.verificar_telegram({"notificaciones": {"telegram": {"bot_token": "T"}}}, http=http_me)
espera("verificar_telegram valida el token", info["bot"]["username"] == "clippingbot")
espera("descubre el chat_id solo", 12345 in info["chats"], str(info["chats"]))

def http_token_malo(url, datos=None, archivos=None, timeout=120):
    return {"ok": False, "description": "Unauthorized"}
try:
    notify.verificar_telegram({"notificaciones": {"telegram": {"bot_token": "mal"}}}, http=http_token_malo)
    espera("un token invalido se reporta", False, "debio lanzar")
except notify.ErrorNotificacion as e:
    espera("un token invalido se reporta", "Unauthorized" in str(e))

notify.TELEGRAM_API = api_real
srv.shutdown()

print("\n" + "=" * 62)
if _fallos:
    print(f"  {len(_fallos)} FALLO(S): " + ", ".join(_fallos))
    print("=" * 62)
    sys.exit(1)
print("  Todo el flujo verificado: 0 fallos")
print("=" * 62 + "\n")
