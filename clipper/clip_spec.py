"""
Genera el brief de edicion de un clip.

Decision de diseno: este modulo NO produce el video. Produce la especificacion
exacta (gancho, estructura por segundo, copy, hashtags, ajustes por plataforma)
que le das a la herramienta de IA de video (OpusClip, CapCut, SendShort, Veo...).

Razon: las herramientas de video con API estable y licencia comercial son pocas y
cambian rapido. Un brief versionado y auditable sobrevive a ese cambio.

Sobre "copiar videos ganadores": aqui se implementa de la unica forma que no te
costa el pago ni la cuenta — se extrae la ESTRUCTURA (longitud del gancho, tipo de
apertura, ritmo de cortes, posicion del CTA) y se aplica sobre material licenciado
por la campana. Copiar el material de otro clipper = duplicado filtrado + ban.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field

# Biblioteca de estructuras de gancho que funcionan en vertical corto.
# Se elige una al azar por clip para que no salgan N posts identicos:
# la deteccion de duplicados castiga la repeticion literal.
GANCHOS = [
    {"id": "pregunta_directa", "seg": 2.5, "plantilla": "¿{dolor}?", "nota": "El dolor del publico objetivo, en sus palabras"},
    {"id": "afirmacion_contraintuitiva", "seg": 3.0, "plantilla": "{beneficio} no requiere {mito}", "nota": "Rompe una creencia del nicho. Solo con material licenciado."},
    # Nota: no hay plantilla de "numero concreto" a proposito. Un marcador {numero}
    # sin fuente invita a inventar la cifra, y un claim inventado hace que la
    # campana rechace el clip y pierdas el pago. Las cifras van en el beat 3 y solo
    # si el brief de la campana las contiene.
    {"id": "antes_despues", "seg": 4.0, "plantilla": "Así era → así quedó", "nota": "Necesita dos tomas del VOD"},
    {"id": "objecion", "seg": 3.0, "plantilla": "\"{objecion}\" — te equivocas", "nota": "Responde la duda #1 del comentario"},
    {"id": "curiosidad_abierta", "seg": 2.5, "plantilla": "Nadie te dice esto sobre {tema}", "nota": "El loop se cierra al final"},
]

AJUSTES_PLATAFORMA = {
    "tiktok": {
        "duracion_objetivo": (15, 34),
        "max_copy_chars": 2200,
        "hashtags": 4,
        "nota": "Sin marca de agua de otras apps. Audio: usar solo sonidos con licencia comercial si la cuenta es Business.",
    },
    "instagram": {
        "duracion_objetivo": (15, 45),
        "max_copy_chars": 2200,
        "hashtags": 5,
        "nota": "Los Reels con audio de la biblioteca personal no califican para cuentas Business.",
    },
    "youtube": {
        "duracion_objetivo": (20, 58),
        "max_copy_chars": 5000,
        "hashtags": 3,
        "nota": "Shorts: el titulo se trunca, el gancho va en el video.",
    },
}


@dataclass
class BriefClip:
    clip_id: str
    campaign_id: str
    plataforma: str
    duracion_seg: float
    gancho: dict
    beats: list[dict] = field(default_factory=list)
    copy: str = ""
    hashtags: list[str] = field(default_factory=list)
    notas_edicion: list[str] = field(default_factory=list)
    disclosure: str = "#ad"

    def as_dict(self) -> dict:
        return asdict(self)

    def markdown(self) -> str:
        l = [f"# Brief de clip — {self.clip_id}",
             f"**Campana:** {self.campaign_id} · **Plataforma:** {self.plataforma} · "
             f"**Duracion objetivo:** {self.duracion_seg:.0f}s", "",
             f"## Gancho (0–{self.gancho['seg']}s)",
             f"- Tipo: `{self.gancho['id']}` — {self.gancho['nota']}",
             f"- Guion: {self.gancho['plantilla']}", "",
             "## Estructura"]
        for b in self.beats:
            l.append(f"- **{b['rango']}** — {b['instruccion']}")
        l += ["", "## Copy / caption", f"```{self.copy}```", "",
              "## Notas para la herramienta de IA de video"]
        l += [f"- {n}" for n in self.notas_edicion]
        l += ["", f"## Divulgacion obligatoria: `{self.disclosure}`"]
        return "\n".join(l)


def nuevo_id_clip(campana_id: str, asset_id: str, plataforma: str, tema: str) -> str:
    """Id estable del clip: mismos insumos -> mismo id (idempotente al regenerar)."""
    import hashlib
    return "cl_" + hashlib.sha1(
        f"{campana_id}:{asset_id}:{plataforma}:{tema}".encode()).hexdigest()[:12]


def generar_brief(clip_id: str, campana: dict, plataforma: str,
                  tema: str, dolor: str, beneficio: str,
                  divulgacion: str = "#ad", semilla: int | None = None,
                  objecion: str = "") -> BriefClip:
    """Construye el brief deterministico a partir de los datos del brief de la campana."""
    rnd = random.Random(semilla if semilla is not None else hash(clip_id) & 0xFFFF)
    aj = AJUSTES_PLATAFORMA.get(plataforma, AJUSTES_PLATAFORMA["tiktok"])
    lo, hi = aj["duracion_objetivo"]
    dur = float(rnd.randint(lo, min(hi, 45)))

    # Ningun marcador puede quedar sin rellenar: un '{objecion}' literal en un copy
    # publicado es un defecto visible, y un marcador numerico invita a inventar datos.
    if not objecion:
        pool = [g for g in GANCHOS if g["id"] != "objecion"]
    else:
        pool = list(GANCHOS)
    gancho = dict(rnd.choice(pool))
    gancho["plantilla"] = (gancho["plantilla"]
                           .replace("{dolor}", dolor)
                           .replace("{beneficio}", beneficio)
                           .replace("{mito}", f"hacerlo {dolor.lower()}")
                           .replace("{objecion}", objecion)
                           .replace("{tema}", tema))
    if "{" in gancho["plantilla"] or "}" in gancho["plantilla"]:
        raise ValueError(f"Marcador sin rellenar en el gancho '{gancho['id']}': {gancho['plantilla']}")

    cuerpo = max(4.0, dur - gancho["seg"] - 3.0)
    t0, t1, t2 = gancho["seg"], gancho["seg"] + cuerpo * 0.6, gancho["seg"] + cuerpo
    beats = [
        {"rango": f"0.0–{t0:.1f}s", "instruccion": "Gancho a pantalla completa. Subtitulo quemado desde el frame 1. Sin intro ni logo."},
        {"rango": f"{t0:.1f}–{t1:.1f}s", "instruccion": f"Demostracion concreta del beneficio: {beneficio}. Corte cada 1.5–2.5s."},
        {"rango": f"{t1:.1f}–{t2:.1f}s", "instruccion": "Prueba o detalle que sostenga la afirmacion (dato del brief de la campana, nunca inventado)."},
        {"rango": f"{t2:.1f}–{dur:.1f}s", "instruccion": "CTA de un solo paso + loop al gancho."},
    ]

    hashtags = _hashtags(campana, aj["hashtags"], rnd, divulgacion)
    linea_extra = (campana.get("marca") or "").strip()
    cierre = f" @{linea_extra.replace(' ', '')}" if linea_extra and not linea_extra.startswith("@") else ""
    copy = (f"{gancho['plantilla']}\n\n"
            f"{beneficio.rstrip('.')}.\n\n"
            f"{' '.join(hashtags)}")
    if len(copy) > aj["max_copy_chars"]:
        copy = copy[: aj["max_copy_chars"] - 1] + "…"

    return BriefClip(
        clip_id=clip_id, campaign_id=campana.get("id", ""), plataforma=plataforma,
        duracion_seg=dur, gancho=gancho, beats=beats, copy=copy, hashtags=hashtags,
        notas_edicion=[
            "Material: SOLO los VOD/assets entregados por la campana (ver tabla assets).",
            f"{aj['nota']}",
            "Variar encuadre, subtitulo y corte entre clips de la misma campana: los duplicados se filtran y no pagan.",
            "No agregar marcas de agua ni logos de la herramienta de edicion.",
            "Exportar 9:16, 1080x1920, H.264, subtitulos quemados.",
            "Guardar el brief y la captura del material de origen: es la evidencia ante una disputa de pago.",
        ],
        disclosure=divulgacion,
    )


def _hashtags(campana: dict, n: int, rnd, divulgacion: str = "#ad") -> list[str]:
    base = [divulgacion or "#ad"]
    cat = (campana.get("categoria") or "").strip().lower()
    if cat:
        base.append("#" + cat.replace(" ", ""))
    marca = (campana.get("marca") or "").strip().lower().replace(" ", "")
    if marca:
        base.append("#" + marca)
    pool = ["#fyp", "#parati", "#shorts", "#reels"]
    rnd.shuffle(pool)
    out = base[:n]
    for h in pool:
        if len(out) >= n:
            break
        if h not in out:
            out.append(h)
    return out


def brief_desde_llm(campana: dict, plataforma: str, tema: str, dolor: str, beneficio: str,
                    llm_cfg: dict | None = None, clip_id: str | None = None,
                    asset_id: str | None = None, objecion: str = "") -> BriefClip:
    """
    Variante opcional: usa un LLM para redactar el gancho y el copy.
    Sin API key cae a la generacion deterministica (que es la que se prueba y audita).
    """
    cid = clip_id or nuevo_id_clip(campana.get("id", ""), asset_id or "", plataforma, tema)
    brief = generar_brief(cid, campana, plataforma, tema, dolor, beneficio, objecion=objecion)
    if not (llm_cfg or {}).get("api_key"):
        brief.notas_edicion.append("LLM no configurado: gancho generado con la biblioteca local.")
        return brief

    try:
        import urllib.request
        payload = json.dumps({
            "model": llm_cfg.get("modelo", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content":
                    "Eres copywriter de video vertical corto. Devuelve JSON con claves 'gancho' "
                    "(max 9 palabras) y 'copy' (max 220 caracteres). No inventes cifras ni claims "
                    "de salud o financieros. Incluye #ad."},
                {"role": "user", "content":
                    f"Marca: {campana.get('marca')}. Categoria: {campana.get('categoria')}. "
                    f"Tema: {tema}. Dolor: {dolor}. Beneficio: {beneficio}. "
                    f"Reglas de la campana: {campana.get('reglas_texto')}"},
            ],
            "temperature": 0.9,
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions", data=payload,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {llm_cfg['api_key']}"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
        txt = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        except Exception:
            parsed = {"gancho": None, "copy": None}
        if parsed.get("gancho"):
            brief.gancho["plantilla"] = parsed["gancho"]
        if parsed.get("copy"):
            brief.copy = parsed["copy"][:2200]
        brief.notas_edicion.append("Gancho y copy generados por LLM: revisa que no invente datos del producto.")
    except Exception as e:  # noqa: BLE001
        brief.notas_edicion.append(f"LLM fallo ({e}); se uso la version deterministica.")
    return brief
