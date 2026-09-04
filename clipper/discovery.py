"""
Descubrimiento y puntaje de campanas.

Importante sobre el scraping: los terminos de Whop y de las plataformas sociales
prohiben el scraping automatico de sitios autenticados. Este modulo por eso:
  * ingesta desde CSV/JSON que TU exportas o capturas con la extension de Chrome,
  * puntua y ordena las campanas,
  * deja un conector (`descubrir_en_navegador`) para que Manus/Cloud Computer lo
    ejecute con tu sesion, si asi lo decides y asumiendo el riesgo de ToS.

La parte que SI es automatica y segura: el puntaje, la deduccion y el reporte.
"""

from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone

from . import compliance, db

CATEGORIAS_PAGO_ALTO = {"tech", "software", "saas", "fintech", "educacion", "fitness", "negocios"}
CATEGORIAS_RIESGO = {"apuestas", "casino", "cripto", "adulto", "salud"}


def puntuar(campana: dict, reglas: dict | None = None) -> float:
    """
    Valor esperado por hora de trabajo, no 'CPM mas alto'.

    La intuccion que casi todos pierden: un CPM de $5 con presupuesto 95% consumido
    paga menos que un CPM de $1.20 con pool fresco. Se pondera por presupuesto
    disponible y por la velocidad tipica de verificacion.
    """
    reglas = compliance.reglas_efectivas(reglas)
    cpm = campana.get("cpm_usd") or 0
    total = campana.get("presupuesto_total") or 0
    rest = campana.get("presupuesto_rest")
    rest = total if rest is None else rest

    if total <= 0:
        disponible = 1.0
    else:
        disponible = max(0.0, rest / total)

    plataformas = compliance.parsear_plataformas(campana.get("plataformas_ok"))
    # Un clip reutilizado en N plataformas multiplica el ingreso del mismo trabajo.
    multi = 1.0 + 0.35 * max(0, len(plataformas) - 1)

    cat = (campana.get("categoria") or "").lower()
    bonus_cat = 1.15 if any(c in cat for c in CATEGORIAS_PAGO_ALTO) else 1.0

    # Una categoria bloqueada no es "peor pagada": es un pasivo legal. Se hunde el
    # puntaje para que no aparezca nunca en un ranking util, ademas del bloqueo
    # explicito que aplica la compuerta.
    bloqueada = bool(compliance._bloqueos_categoria(cat, reglas.get("bloquear_categorias", [])))
    penal_cat = 0.05 if bloqueada else (0.6 if any(c in cat for c in CATEGORIAS_RIESGO) else 1.0)

    waitlist = 0.7 if campana.get("requiere_waitlist") else 1.0

    # log1p evita que una campana con pool enorme domine todo el ranking.
    escala = math.log1p(max(rest, 0) / 1000.0)

    return round(cpm * escala * disponible * multi * bonus_cat * penal_cat * waitlist, 3)


def importar_csv(ruta: str, plataforma: str = "whop", reglas: dict | None = None) -> dict:
    """
    Columnas esperadas (todas opcionales menos url):
      url, titulo, marca, categoria, cpm_usd, presupuesto_total, presupuesto_rest,
      plataformas_ok (separadas por |), min_views, cap_por_clip_usd,
      requiere_waitlist (0/1), reglas_texto, fecha_fin
    """
    nuevos, actualizados, errores = 0, 0, 0
    with open(ruta, newline="", encoding="utf-8-sig") as f:
        for i, fila in enumerate(csv.DictReader(f), start=2):
            try:
                upsert_campana(fila, plataforma, reglas=reglas)
                nuevos += 1
            except Exception as e:  # noqa: BLE001
                errores += 1
                print(f"  ! linea {i}: {e}")
    return {"importadas": nuevos + actualizados, "errores": errores}


def upsert_campana(fila: dict, plataforma: str = "whop", conn=None, reglas: dict | None = None) -> str:
    url = (fila.get("url") or "").strip()
    if not url:
        raise ValueError("falta la columna 'url'")

    plataformas_ok = fila.get("plataformas_ok") or "[]"
    if isinstance(plataformas_ok, str) and not plataformas_ok.startswith("["):
        plataformas_ok = json.dumps([p.strip().lower() for p in plataformas_ok.split("|") if p.strip()])

    def num(clave):
        v = fila.get(clave)
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None

    datos = {
        "plataforma": fila.get("plataforma") or plataforma,
        "url": url,
        "marca": fila.get("marca"),
        "titulo": fila.get("titulo"),
        "categoria": fila.get("categoria"),
        "cpm_usd": num("cpm_usd"),
        "presupuesto_total": num("presupuesto_total"),
        "presupuesto_rest": num("presupuesto_rest"),
        "plataformas_ok": plataformas_ok,
        "min_views": int(num("min_views") or 0),
        "cap_por_clip_usd": num("cap_por_clip_usd"),
        "requiere_waitlist": 1 if str(fila.get("requiere_waitlist", "0")) in ("1", "true", "si", "yes") else 0,
        "reglas_texto": fila.get("reglas_texto"),
        "fecha_fin": fila.get("fecha_fin"),
        "notas": fila.get("notas"),
    }
    datos["puntaje"] = puntuar(datos, reglas)
    cid = id_de_campana(url)

    def _exec(c):
        existe = db.uno(c, "SELECT id, estado FROM campaigns WHERE url = ?", (url,))
        if existe:
            # Solo se sobreescriben los campos que vienen con valor. Un upsert con
            # fila parcial (p.ej. solo la URL) NO debe vaciar el CPM ni el pool que
            # ya teniamos cargados.
            parciales = {k: v for k, v in datos.items() if v is not None}
            if parciales:
                campos = ", ".join(f"{k} = excluded.{k}" for k in parciales)
                c.execute(
                    f"""INSERT INTO campaigns (id, {", ".join(datos)})
                        VALUES (?, {", ".join("?" * len(datos))})
                        ON CONFLICT(id) DO UPDATE SET {campos}""",
                    (cid, *datos.values()),
                )
            db.log(c, "campana_actualizada", {"id": cid, "campos": list(parciales)})
            return existe["id"], False
        c.execute(
            f"""INSERT INTO campaigns (id, visto_por_primera_vez, estado, {", ".join(datos)})
                VALUES (?, ?, 'nueva', {", ".join("?" * len(datos))})""",
            (cid, db.ahora(), *datos.values()),
        )
        db.log(c, "campana_nueva", {"id": cid, "puntaje": datos["puntaje"]})
        return cid, True

    if conn is not None:
        return _exec(conn)[0]
    with db.sesion() as c:
        return _exec(c)[0]


def id_de_campana(url: str) -> str:
    import hashlib
    return "c_" + hashlib.sha1(url.encode()).hexdigest()[:12]


def mejores_campanas(limite: int = 10, minimo_puntaje: float = 0.0,
                     aplicar_compuerta: bool = True, reglas: dict | None = None) -> list[dict]:
    """Ranking por valor esperado.

    Con `aplicar_compuerta=True` (defecto) se descarta lo que compliance bloquearia:
    de nada sirve encabezar un ranking con una campana que no vas a poder operar.
    """
    with db.sesion() as c:
        filas = db.filas(c.execute(
            """SELECT * FROM campaigns
               WHERE estado IN ('nueva','vista','aprobada','activa') AND puntaje >= ?
               ORDER BY puntaje DESC, presupuesto_rest DESC LIMIT ?""",
            (minimo_puntaje, limite * 4 if aplicar_compuerta else limite),
        ))
    if not aplicar_compuerta:
        return filas[:limite]
    reglas = compliance.reglas_efectivas(reglas)
    out = []
    for f in filas:
        if compliance.revisar_campana(f, reglas).ok:
            out.append(f)
        if len(out) >= limite:
            break
    return out


def registrar_asset(campaign_id: str, tipo: str, ruta_o_url: str, licencia: str = "campana",
                    duracion_seg: float | None = None, licencia_prueba: str | None = None) -> str:
    """
    `licencia` aceptada: 'campana' (la entrego la marca), 'propia', 'cc0', 'licenciada'.
    Cualquier otro valor bloquea el clip en compliance.
    """
    import hashlib
    aid = "a_" + hashlib.sha1(f"{campaign_id}:{ruta_o_url}".encode()).hexdigest()[:12]
    with db.sesion() as c:
        c.execute(
            """INSERT INTO assets (id, campaign_id, tipo, ruta_o_url, duracion_seg, licencia, licencia_prueba, descargado)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET licencia=excluded.licencia, licencia_prueba=excluded.licencia_prueba""",
            (aid, campaign_id, tipo, ruta_o_url, duracion_seg, licencia, licencia_prueba,
             1 if os.path.exists(ruta_o_url) else 0),
        )
        db.log(c, "asset_registrado", {"id": aid, "licencia": licencia})
    return aid


def descubrir_en_navegador() -> None:
    """
    Punto de integracion para Manus / Cloud Computer.

    Este intencionadamente NO hace scraping por su cuenta. La instruccion que va en
    `docs/manus/INSTRUCCION.md` le pide a Manus que navegue con tu sesion, lea los
    datos visibles de cada campana y devuelva un CSV con el formato de
    `importar_csv`. A partir de ahi todo es deterministico y auditable.
    """
    raise NotImplementedError(
        "La deteccion en navegador la ejecuta Manus con tu sesion y devuelve un CSV. "
        "Usa discovery.importar_csv(ruta) para cargarlo. Ver docs/manus/INSTRUCCION.md paso 1."
    )
