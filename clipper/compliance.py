"""
Compuerta de cumplimiento. Es el corazon del sistema.

Nada se produce ni se publica sin pasar por aqui. Cada regla existe porque una
plataforma concreta castiga la violacion, y el castigo tipico es perder el saldo
pendiente de pago (Whop) o la cuenta entera (TikTok/Instagram).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HASHTAGS_DIVULGACION = {"#ad", "#ads", "#sponsored", "#publi", "#publicidad", "#paidpartnership"}

# Reglas duras POR DEFECTO. No dependen de config.json: un archivo de configuracion
# ausente o a medias no puede desactivar la proteccion legal del operador.
REGLAS_POR_DEFECTO = {
    "solo_assets_licenciados_por_campana": True,
    "prohibido_clonar_videos_de_otros_clippers": True,
    "divulgacion_obligatoria": "#ad",
    "marcas_agua_prohibidas": True,
    "cpm_minimo_usd": 0.5,
    "pool_max_consumido": 0.8,
    "bloquear_categorias": ["apuestas", "casino", "cripto sin registro", "adulto", "salud"],
}


def reglas_efectivas(cfg: dict | None = None) -> dict:
    """Fusiona config.json sobre las reglas por defecto, sin poder desactivarlas.

    Una lista de `bloquear_categorias` en el config SIEMPRE se une a la por defecto:
    si alguien la deja vacia por error, el filtro no desaparece.
    """
    out = dict(REGLAS_POR_DEFECTO)
    for k, v in (cfg or {}).items():
        if k == "bloquear_categorias":
            extra = [str(x).lower() for x in (v or [])]
            out[k] = sorted(set(out[k]) | set(extra))
        elif k in ("solo_assets_licenciados_por_campana", "prohibido_clonar_videos_de_otros_clippers",
                   "divulgacion_obligatoria", "marcas_agua_prohibidas") and v in (False, "", None):
            continue  # las protecciones no se apagan desde el config
        else:
            out[k] = v
    return out

# Frases que las campanas suelen prohibir explicitamente (claims prohibidos).
CLAIMS_RIESGOSOS = [
    r"\bgarantizado\b", r"\briesgo cero\b", r"\bsin riesgo\b", r"\bget rich quick\b",
    r"\bcura\b", r"\bcancer\b", r"\brentabilidad asegurada\b", r"\bx10\b", r"\b100x\b",
    r"\bprueba social falsa\b",
]


@dataclass
class Veredicto:
    ok: bool
    bloqueos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "bloqueos": self.bloqueos, "avisos": self.avisos}


def _bloqueos_categoria(categoria: str | None, bloquear: list[str]) -> list[str]:
    if not categoria:
        return []
    cat = categoria.lower()
    return [f"Categoria '{categoria}' esta en la lista de bloqueo por riesgo legal/reputacional"
            for b in bloquear if b.lower() in cat]


def parsear_plataformas(valor) -> list[str]:
    """Acepta JSON ('["tiktok","instagram"]') o separado por barras ('tiktok|instagram').

    Antes solo se intentaba JSON: un valor con '|' quedaba en lista vacia y la
    comprobacion de plataformas elegibles se saltaba en silencio.
    """
    if not valor:
        return []
    if isinstance(valor, (list, tuple)):
        return [str(p).strip().lower() for p in valor if str(p).strip()]
    s = str(valor).strip()
    if s.startswith("["):
        import json as _j
        try:
            return parsear_plataformas(_j.loads(s))
        except Exception:
            return []
    return [p.strip().lower() for p in s.split("|") if p.strip()]


def revisar_campana(campana: dict, reglas: dict | None = None) -> Veredicto:
    """Evalua si vale la pena entrar a una campana ANTES de invertir tiempo."""
    reglas = reglas_efectivas(reglas)
    v = Veredicto(ok=True)

    cpm = campana.get("cpm_usd") or 0
    rest = campana.get("presupuesto_rest")
    total = campana.get("presupuesto_total")

    if cpm < (reglas.get("cpm_minimo_usd") or 0.5):
        v.bloqueos.append(
            f"CPM ${cpm}/1k por debajo de ${reglas.get('cpm_minimo_usd') or 0.5}: el esfuerzo no se paga "
            "(mercado tipico $0.20-$6, media ~$1)")
    if rest is not None and total and total > 0:
        gastado = (total - rest) / total
        if gastado >= (reglas.get("pool_max_consumido") or 0.8):
            v.bloqueos.append(f"Presupuesto {gastado:.0%} consumido: las vistas verifican tarde y el pool se seca antes")
        elif gastado >= 0.6:
            v.avisos.append(f"Presupuesto {gastado:.0%} consumido: publicar en las proximas 24-48h o descartar")
    if campana.get("min_views", 0) > 2000:
        v.avisos.append(f"Umbral minimo de {campana['min_views']} vistas: por debajo de eso el clip paga $0")
    if campana.get("requiere_waitlist"):
        v.avisos.append("Requiere waitlist: la ventana de 'pool lleno' probablemente ya paso")

    v.bloqueos.extend(_bloqueos_categoria(campana.get("categoria"), reglas.get("bloquear_categorias", [])))

    plataformas = parsear_plataformas(campana.get("plataformas_ok"))
    if plataformas and not ({"tiktok", "instagram", "reels"} & set(plataformas)):
        v.bloqueos.append(f"Ninguna de tus cuentas esta entre las plataformas elegibles: {plataformas}")

    v.ok = not v.bloqueos
    return v


def revisar_clip(clip: dict, campana: dict, assets: list[dict], reglas: dict | None = None) -> Veredicto:
    """Compuerta obligatoria antes de renderizar o publicar un clip."""
    reglas = reglas_efectivas(reglas)
    v = Veredicto(ok=True)

    # 1. La campana debe estar viva y aprobada por la compuerta anterior.
    if campana.get("estado") not in ("activa", "aprobada", "en_produccion"):
        v.bloqueos.append(f"Campana en estado '{campana.get('estado')}': no esta aprobada para producir")

    # 2. El material debe estar licenciado por la campana. Esta es LA regla que
    #    separa clipping legal (te pagan) de re-subida no autorizada (te banean).
    origen = clip.get("asset_origen")
    if not origen:
        v.bloqueos.append("Sin asset_origen: no se puede probar de donde salio el material")
    else:
        permitido = {a["id"]: a for a in assets}
        a = permitido.get(origen)
        if a is None:
            v.bloqueos.append(f"Asset '{origen}' no esta registrado en la campana: material sin licencia")
        elif a.get("licencia") not in ("campana", "propia", "cc0", "licenciada"):
            v.bloqueos.append(
                f"Asset con licencia '{a.get('licencia')}': solo se permite material entregado por la campana, "
                "tuyo, o con licencia verificable"
            )
        elif not a.get("descargado"):
            v.avisos.append("Asset no marcado como descargado: guarda la copia local como evidencia de la licencia")

    if reglas.get("prohibido_clonar_videos_de_otros_clippers", True) and _parece_copia(clip):
        v.bloqueos.append(
            "El clip parece clon de otro clipper. Whop filtra duplicados y TikTok detecta re-subidas: "
            "cero pago y riesgo de ban. Presta la ESTRUCTURA (hook/ritmo), no el material."
        )

    # 3. Divulgacion de contenido comercial. Es contenido pagado: debe decirlo.
    texto = " ".join(filter(None, [clip.get("copy"), clip.get("titulo")])).lower()
    if reglas.get("divulgacion_obligatoria") and not (HASHTAGS_DIVULGACION & set(re.findall(r"#\w+", texto))):
        v.bloqueos.append("Falta divulgacion de contenido pagado (#ad / #sponsored / #publi) en el copy")

    # 4. Claims prohibidos del brief de la campana.
    for pat in CLAIMS_RIESGOSOS:
        if re.search(pat, texto):
            v.bloqueos.append(f"Claim de riesgo en el copy: '{pat}'. Las campanas rechazan esto y te quitan el pago")

    # 5. Reglas de la plataforma.
    if clip.get("marca_agua") and reglas.get("marcas_agua_prohibidas", True):
        v.bloqueos.append("Marca de agua detectada: TikTok rechaza videos con marcas de agua de otras apps")
    dur = clip.get("duracion_seg") or 0
    if dur and dur < 3:
        v.bloqueos.append(f"Duracion {dur}s: TikTok exige minimo 3 segundos")
    if dur and dur > 180:
        v.avisos.append(f"Duracion {dur}s: muy largo para retention; el rango que paga es 15-45s")

    # 6. Reglas especificas de la campana (texto libre del brief).
    reglas_txt = (campana.get("reglas_texto") or "").lower()
    if reglas_txt:
        if "no music" in reglas_txt or "sin musica" in reglas_txt:
            if clip.get("usa_musica"):
                v.bloqueos.append("La campana prohibe musica y el clip la usa")
        if "hashtag requerido" in reglas_txt or "required hashtag" in reglas_txt:
            v.avisos.append("La campana exige hashtags especificos: revisa el brief antes de publicar")

    v.ok = not v.bloqueos
    return v


def _parece_copia(clip: dict) -> bool:
    """Heuristica: marca como copia si el propio flujo declaro que viene de otro post."""
    src = (clip.get("asset_origen") or "").lower()
    if src.startswith("url_externa") or src.startswith("tiktok.com") or src.startswith("instagram.com"):
        return True
    return bool(clip.get("clonado_de"))
