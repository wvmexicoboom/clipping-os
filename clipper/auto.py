"""
Ciclo autonomo de produccion.

Esto es lo que puede correr solo, y lo que NO:

  PUEDE correr solo
    - elegir la campana con mejor valor esperado que pase la compuerta
    - generar los briefs por plataforma
    - enviar a una herramienta CON API (hoy: OpusClip) y consultar el estado
    - re-ingestar el render, pasar la compuerta y dejarlo en la cola
    - llevar contabilidad de cuanto se hizo y reportar

  NO corre solo, nunca
    - publicar. El clic final es del operador (ver publish.py).
    - saltarse la compuerta. Cada clip pasa compliance, y el detector de marca
      de agua puede dejarlo en 'revision_agua'.
    - usar una herramienta sin API. SendShort y CapCut no tienen endpoint: ahi
      el lote prepara la carpeta de trabajo y avisa.
    - superar el tope diario. `max_por_cuenta_dia` existe porque publicar de mas
      es justo el patron que dispara la deteccion de spam.

El tope diario se persiste en la base por fecha UTC, asi que reiniciar el proceso
no lo reinicia.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import clip_spec, compliance, db, discovery, providers, publish

TOPE_DURO_DIA = 25          # limite absoluto por cuenta y dia, no configurable por encima


@dataclass
class ResultadoLote:
    producidos: int = 0
    encolados: int = 0
    bloqueados: int = 0
    en_revision_agua: int = 0
    carpetas_preparadas: int = 0
    omitidos: int = 0
    detalles: list[str] = field(default_factory=list)
    error: str | None = None

    def resumen(self) -> str:
        l = ["=" * 62, "  LOTE DE PRODUCCION", "=" * 62,
             f"  Clips producidos ......... {self.producidos}",
             f"  Encolados (listos) ....... {self.encolados}",
             f"  Bloqueados por compliance  {self.bloqueados}",
             f"  En revision de agua ...... {self.en_revision_agua}",
             f"  Carpetas para operacion manual {self.carpetas_preparadas}",
             f"  Omitidos ................. {self.omitidos}"]
        if self.error:
            l.append(f"  ERROR: {self.error}")
        l.append("-" * 62)
        l += [f"  · {d}" for d in self.detalles]
        l.append("=" * 62)
        return "\n".join(l)


def _hoy() -> str:
    return db.ahora()[:10]


def _usados_hoy(cuenta: str) -> int:
    with db.sesion() as c:
        r = db.uno(c, "SELECT usados FROM auto_state WHERE fecha=? AND cuenta=?", (_hoy(), cuenta))
    return r["usados"] if r else 0


def _consumir(cuenta: str, n: int = 1) -> None:
    with db.sesion() as c:
        c.execute("""INSERT INTO auto_state (fecha, cuenta, usados) VALUES (?,?,?)
                     ON CONFLICT(fecha, cuenta) DO UPDATE SET usados = usados + excluded.usados""",
                  (_hoy(), cuenta, n))


def _reglas(cfg: dict) -> dict:
    return compliance.reglas_efectivas((cfg or {}).get("reglas_duras", {}))


def elegir_trabajo(cfg: dict, limite: int = 3) -> list[dict]:
    """Campanas aprobables con material licenciado, en orden de valor esperado."""
    reglas = _reglas(cfg)
    con_assets = []
    for camp in discovery.mejores_campanas(limite * 3, reglas=reglas):
        with db.sesion() as c:
            assets = db.filas(c.execute(
                "SELECT * FROM assets WHERE campaign_id=? AND licencia IN "
                "('campana','propia','cc0','licenciada')", (camp["id"],)))
        if assets:
            camp["_assets"] = assets
            con_assets.append(camp)
        if len(con_assets) >= limite:
            break
    return con_assets


def _brief_para(camp: dict, asset: dict, plataforma: str, cfg: dict) -> clip_spec.BriefClip:
    """Deriva tema/dolor/beneficio del brief de la campana en vez de pedirlos a mano."""
    reglas_txt = camp.get("reglas_texto") or ""
    tema = (camp.get("categoria") or camp.get("marca") or "el producto").strip()
    dolor = camp.get("titulo") or tema
    beneficio = camp.get("marca") or tema
    cid = clip_spec.nuevo_id_clip(camp["id"], asset["id"], plataforma, tema)
    return clip_spec.brief_desde_llm(camp, plataforma, tema, dolor, beneficio,
                                     (cfg or {}).get("llm"), clip_id=cid, asset_id=asset["id"])


def _registrar_clip(brief, camp: dict, asset: dict) -> str:
    with db.sesion() as c:
        c.execute("""INSERT INTO clips (id, campaign_id, titulo, hook, copy, duracion_seg,
                     asset_origen, estado, creado_en)
                     VALUES (?,?,?,?,?,?,?,'en_produccion',?)
                     ON CONFLICT(id) DO UPDATE SET copy=excluded.copy, hook=excluded.hook""",
                  (brief.clip_id, camp["id"], brief.gancho["plantilla"], brief.gancho["plantilla"],
                   brief.copy, brief.duracion_seg, asset["id"], db.ahora()))
        db.log(c, "clip_automatico", {"clip": brief.clip_id, "campana": camp["id"]})
    return brief.clip_id


def lote(cfg: dict, max_clips: int = 3, plataforma: str = "tiktok",
         cuenta: str = "principal", proveedor_pref: str | None = None,
         dry_run: bool = False) -> ResultadoLote:
    """Un ciclo completo de produccion. No publica nada."""
    r = ResultadoLote()
    cfg = cfg or {}
    auto = (cfg.get("auto") or {})
    tope = min(int(auto.get("max_por_cuenta_dia", 8)), TOPE_DURO_DIA)

    if _usados_hoy(cuenta) >= tope:
        r.error = (f"Tope diario alcanzado para '{cuenta}': {_usados_hoy(cuenta)}/{tope}. "
                   f"Publicar de mas es el patron que dispara la deteccion de spam.")
        return r
    disponible = max(0, tope - _usados_hoy(cuenta))
    max_clips = min(max_clips, disponible)
    if max_clips <= 0:
        r.error = "Nada que hacer en este lote."
        return r

    trabajos = elegir_trabajo(cfg, max_clips)
    if not trabajos:
        r.error = ("No hay campanas operables. Hace falta una campana que pase la compuerta "
                   "Y tenga material licenciado registrado (`main.py asset ... --licencia campana`).")
        return r

    prov_nombre = proveedor_pref or (auto.get("proveedor") or "opusclip")
    try:
        prov = providers.obtener(prov_nombre, cfg)
    except providers.ErrorProveedor as e:
        r.error = str(e)
        return r

    hechos = 0
    for camp in trabajos:
        if hechos >= max_clips:
            break
        for asset in camp["_assets"]:
            if hechos >= max_clips:
                break
            brief = _brief_para(camp, asset, plataforma, cfg)
            cid = _registrar_clip(brief, camp, asset)
            r.producidos += 1
            hechos += 1

            if dry_run:
                r.detalles.append(f"{cid}: dry-run, brief generado para {camp.get('marca')}")
                continue

            if not prov.tiene_api:
                # Sin API no hay nada que automatizar: se prepara la carpeta y se avisa.
                destino = f"{publish.RUTA_SALIDA}/trabajo_{prov.nombre}_{cid}"
                prov.preparar_carpeta(asset, brief, camp, destino)
                r.carpetas_preparadas += 1
                r.detalles.append(f"{cid}: {prov.nombre} no tiene API → carpeta lista en {destino}. "
                                  f"Operalo a mano y luego `main.py render`.")
                continue

            if not prov.configurado():
                r.omitidos += 1
                r.detalles.append(f"{cid}: {prov.nombre} sin credencial. Guardala con "
                                  f"`main.py secretos-set --proveedor {prov.nombre} --campo api_key`.")
                continue

            try:
                env = prov.enviar(asset, brief, camp)
                providers.registrar_trabajo(prov.nombre, camp["id"], asset["id"],
                                            env.get("project_id", ""), "enviado",
                                            {"clip_id": cid})
                r.detalles.append(f"{cid}: enviado a {prov.nombre}, proyecto {env.get('project_id')}")
            except providers.ErrorProveedor as e:
                r.omitidos += 1
                r.detalles.append(f"{cid}: {prov.nombre} rechazo el envio — {str(e)[:110]}")
    return r


def cosechar(cfg: dict, cuenta: str = "principal", dry_run: bool = False) -> ResultadoLote:
    """
    Revisa los trabajos enviados, y cuando la herramienta termino, re-ingesta el
    render, pasa la compuerta y deja el clip en la cola. Aqui es donde el detector
    de marca de agua puede frenar todo, y debe poder frenarlo.
    """
    r = ResultadoLote()
    cfg = cfg or {}
    auto = (cfg.get("auto") or {})
    tope = min(int(auto.get("max_por_cuenta_dia", 8)), TOPE_DURO_DIA)
    plataforma = auto.get("plataforma", "tiktok")

    for t in providers.trabajos(pendiente=True):
        try:
            prov = providers.obtener(t["proveedor"], cfg)
            est = prov.estado(t)
        except providers.ErrorProveedor as e:
            r.detalles.append(f"trabajo #{t['id']}: sin estado — {str(e)[:90]}")
            continue

        if not est.get("listo"):
            r.detalles.append(f"trabajo #{t['id']}: aun procesando")
            continue

        extra = json.loads(t.get("extra") or "{}")
        clip_id = extra.get("clip_id")
        clips = est.get("clips") or []
        url = None
        for c in clips:
            url = c.get("url") or c.get("videoUrl") or c.get("downloadUrl") or c.get("src")
            if url:
                break
        providers.actualizar_trabajo(t["id"], "listo", est.get("n_clips"), est)

        if not url:
            r.detalles.append(f"trabajo #{t['id']}: listo pero la respuesta no trae URL de descarga. "
                              f"Descargalo a mano y usa `main.py render`.")
            continue
        if dry_run:
            r.detalles.append(f"trabajo #{t['id']}: dry-run, hay {est.get('n_clips')} clip(s) en {url}")
            continue

        # La herramienta devuelve una URL: la compuerta necesita el archivo local
        # para medir marca de agua, asi que se descarga antes de decidir nada.
        local = _descargar(url, clip_id or f"job{t['id']}")
        if not local:
            r.detalles.append(f"trabajo #{t['id']}: no se pudo descargar {url}")
            continue

        try:
            providers.registrar_render(clip_id, local, t["proveedor"])
        except providers.ErrorProveedor as e:
            r.en_revision_agua += 1
            r.detalles.append(f"{clip_id}: FRENADO — {str(e)[:130]}")
            continue

        with db.sesion() as c:
            clip = db.uno(c, "SELECT * FROM clips WHERE id=?", (clip_id,))
            camp = db.uno(c, "SELECT * FROM campaigns WHERE id=?", (clip["campaign_id"],))
            assets = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=?", (camp["id"],)))
        try:
            publish.encolar_clip(clip, camp, assets, plataforma, cuenta, _reglas(cfg))
            _consumir(cuenta)
            r.encolados += 1
            r.detalles.append(f"{clip_id}: en la cola para {plataforma} ({_usados_hoy(cuenta)}/{tope} hoy)")
        except publish.ErrorPublicacion as e:
            r.bloqueados += 1
            r.detalles.append(f"{clip_id}: bloqueado por compliance — {str(e)[:130]}")
    return r


def _descargar(url: str, nombre: str) -> str | None:
    import urllib.request
    destino = f"{publish.RUTA_SALIDA}/render_{nombre}.mp4"
    import os
    os.makedirs(publish.RUTA_SALIDA, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=600) as r, open(destino, "wb") as f:
            f.write(r.read())
        return destino if os.path.getsize(destino) > 0 else None
    except Exception:
        return None


def vigilar(cfg: dict, intervalo_seg: int = 900, ciclos: int = 0, cuenta: str = "principal"):
    """
    Bucle de produccion + cosecha. `ciclos=0` significa para siempre.

    Pensado para correr en el Cloud Computer de Manus o en un cron propio, no para
    dejarse corriendo a ciegas: cada ciclo imprime su resultado.
    """
    n = 0
    while ciclos == 0 or n < ciclos:
        n += 1
        print(f"\n[ciclo {n}] {db.ahora()}")
        a = cosechar(cfg, cuenta)          # primero lo que ya esta listo
        b = lote(cfg, cuenta=cuenta)       # despues se produce lo que falta
        print(a.resumen())
        print(b.resumen())
        if ciclos == 0 or n < ciclos:
            time.sleep(intervalo_seg)
    return n
