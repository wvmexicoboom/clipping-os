"""
Kit de publicacion: todo lo que rodea al clip y decide si alguien lo ve.

Un clip bien editado con un titulo flojo no se distribuye. Este modulo genera,
para cada borrador, el paquete completo listo para copiar y pegar:

    titulos_propuestos · descripcion · hashtags por nivel · gancho ·
    miniatura · primer comentario · ventana de publicacion · checklist

Decision de diseno importante — nada de esto promete viralidad. Cada elemento
lleva un campo `evidencia` que dice de donde sale, y hay dos categorias:

  * "restriccion de plataforma"  → es una regla dura (limite de caracteres,
    truncamiento, conteo de hashtags). Verificable en la documentacion.
  * "heuristica"                 → es un patron que suele funcionar. No esta
    garantizado y depende de tu audiencia. Se etiqueta como tal, sin adornos.

Por que separarlos: si te presento una heuristica con voz de hecho, vas a
optimizar para algo que no existe. Y en este negocio el dato duro que si tenemos
es que el promedio de ingreso por clipper es bajo, asi que la honestidad sobre
que funciona y que no es parte del producto, no un detalle.

Deterministico: mismos insumos -> mismo kit. Sin LLM obligatorio (si configuras
uno, `enriquecer_con_llm()` lo mejora, pero el modulo funciona sin el).
"""

from __future__ import annotations

import json
import random

from .clip_spec import AJUSTES_PLATAFORMA

# ---------------------------------------------------------------------------
# Ventanas de publicacion. Etiquetadas como heuristica a proposito: los estudios
# de "mejor hora" son ruidosos y dependen de la zona horaria de TU audiencia.
# Se dan en hora del usuario, no en UTC.
# ---------------------------------------------------------------------------
VENTANAS = {
    "tiktok": [
        {"ventana": "06:00–09:00", "por_que": "Heuristica: consumo en el trayecto. Alta competencia, pero el algoritmo aun esta repartiendo."},
        {"ventana": "12:00–14:00", "por_que": "Heuristica: pausa de comida. Buen equilibrio alcance/competencia."},
        {"ventana": "19:00–22:00", "por_que": "Heuristica: pico de sesion. Mas vistas potenciales, pero compites contra todo el mundo."},
    ],
    "instagram": [
        {"ventana": "11:00–13:00", "por_que": "Heuristica: Reels se consume mas en pausa diurna que en la noche."},
        {"ventana": "18:00–20:00", "por_que": "Heuristica: transicion trabajo-casa."},
    ],
    "youtube": [
        {"ventana": "15:00–18:00", "por_que": "Heuristica: Shorts se indexa con retraso; publicar antes del pico ayuda."},
        {"ventana": "20:00–23:00", "por_que": "Heuristica: segunda sesion larga del dia."},
    ],
}

# Frases que el algoritmo y los moderadores castigan, y que ademas suelen ser
# claims sin fuente. Se detectan y se avisa.
FRAGILES = [
    ("gratis", "La palabra 'gratis' sin contexto dispara revision y reduce alcance"),
    ("garantizado", "Claim absoluto: riesgo de rechazo por la campana y de reporte"),
    ("100%", "Cifra absoluta sin fuente: la campana puede rechazar el clip"),
    ("milagro", "Promesa imposible: el filtro de contenido lo marca"),
    ("hazte rico", "Promesa de ingreso: motivo frecuente de ban"),
    ("seguidores", "Pedir seguidores explicitamente se lee como engagement bait"),
    ("link en bio", "En TikTok reduce alcance; en Instagram es neutro"),
    ("#fyp", "No distribuye por si solo; ocupa un lugar que podria ir a un hashtag de nicho"),
    ("suscribete", "Fuera de contexto en TikTok/Reels; baja retencion"),
]

PLANTILLAS_TITULO = [
    {"id": "tension", "molde": "{beneficio} (sin {dolor})",
     "por_que": "Heuristica: promete el resultado y elimina el costo percibido en la misma linea."},
    {"id": "contraintuitivo", "molde": "Lo que nadie dice sobre {tema}",
     "por_que": "Heuristica: abre un vacio de informacion que solo se cierra viendo el video."},
    {"id": "especifico", "molde": "{beneficio} en {duracion}s",
     "por_que": "Restriccion de plataforma: la duracion real es un dato verificable, no un claim inventado."},
    {"id": "segunda_persona", "molde": "Si {dolor}, mira esto",
     "por_que": "Heuristica: el filtro de relevancia lo hace el propio espectador al sentirse aludido."},
    {"id": "lista_corta", "molde": "3 errores al {tema}",
     "por_que": "Heuristica: estructura numerada sostiene la retencion hasta el final. Solo si el clip realmente lista 3."},
]


def _limpiar(texto: str) -> str:
    return " ".join((texto or "").split()).strip()


def _frase_corta(texto: str, max_palabras: int = 6, max_car: int = 42) -> str:
    """Recorta por palabras, nunca a media palabra.

    Cortar a los 18 caracteres produce '#EpsilonSaaSnorequi': basura que igual se
    iria publicada. Mejor una frase completa y corta que un fragmento.
    """
    palabras = _limpiar(texto).split()[:max_palabras]
    frase = " ".join(palabras)
    while len(frase) > max_car and len(palabras) > 1:
        palabras.pop()
        frase = " ".join(palabras)
    return frase.rstrip(" ,.;:")


def _slug(texto: str, max_palabras: int = 3, minusculas: bool = True) -> str:
    """Para hashtags: sin signos, cortado por palabras completas.

    `minusculas=False` conserva el CamelCase de las marcas (#EpsilonSaaS): los
    hashtags no distinguen mayusculas, pero en minusculas la marca se vuelve
    ilegible. Para temas genericos si conviene minusculas.
    """
    import re
    limpio = re.sub(r"[^\w\s]", "", _frase_corta(texto, max_palabras, 24))
    if minusculas:
        limpio = limpio.lower()
    return "".join(limpio.split())


def detectar_fragiles(texto: str) -> list[dict]:
    """Devuelve las frases de riesgo presentes en un texto."""
    bajo = (texto or "").lower()
    return [{"frase": f, "razon": r} for f, r in FRAGILES if f in bajo]


def generar_kit(clip: dict, campana: dict, plataforma: str = "tiktok",
                tema: str = "", dolor: str = "", beneficio: str = "",
                semilla: int | None = None) -> dict:
    """
    Construye el kit completo de publicacion para un clip.

    Los campos tema/dolor/beneficio se pueden omitir: si el clip ya trae `hook`
    y `copy` generados, se derivan de ahi. Nunca se inventan cifras.
    """
    aj = AJUSTES_PLATAFORMA.get(plataforma, AJUSTES_PLATAFORMA["tiktok"])
    rnd = random.Random(semilla if semilla is not None else (hash(clip.get("id", "")) & 0xFFFF))

    marca = _limpiar(campana.get("marca") or "")
    categoria = _limpiar(campana.get("categoria") or "")
    tema = tema or _limpiar(clip.get("titulo") or "") or categoria or marca or ""
    dolor = _limpiar(dolor)
    beneficio = _limpiar(beneficio) or _limpiar(clip.get("hook") or "")
    dur = int(clip.get("duracion_seg") or rnd.randint(*aj["duracion_objetivo"]))

    # ---- Guarda contra insumos degenerados ---------------------------------
    # Un titulo armado sobre datos vacios sale como "t (sin te cuesta)": texto
    # inservible que igual se iria al caption del video. Mejor detectarlo y
    # marcarlo que publicarlo. MIN_CAR es corto a proposito: solo descarta lo
    # que claramente no es lenguaje util.
    MIN_CAR = 8
    MAX_CAR_FRASE = 42
    faltantes = []
    # Un "beneficio" de 60 caracteres no es una frase de titulo, es un parrafo.
    # Incrustarlo produce titulos ilegibles, asi que se avisa y se recorta.
    beneficio_largo = len(beneficio) > MAX_CAR_FRASE
    dolor_largo = len(dolor) > MAX_CAR_FRASE
    tema_largo = len(tema) > MAX_CAR_FRASE
    if len(tema) < MIN_CAR:
        faltantes.append("tema")
    if len(beneficio) < MIN_CAR:
        faltantes.append("beneficio")
    if len(dolor) < MIN_CAR:
        faltantes.append("dolor")
    if beneficio_largo:
        faltantes.append("beneficio (demasiado largo para un titulo)")
    if tema_largo:
        faltantes.append("tema (demasiado largo para un titulo)")
    if dolor_largo:
        faltantes.append("dolor (demasiado largo para un titulo)")
    # Rellenos neutros para que el kit siga siendo legible, nunca inventados:
    # se usan solo en plantillas que no afirman nada verificable.
    tema = tema if len(tema) >= MIN_CAR else (categoria or marca or "este tema")
    beneficio = beneficio if len(beneficio) >= MIN_CAR else "el resultado del video"
    dolor = dolor if len(dolor) >= MIN_CAR else "lo que te esta costando"

    # ---- Titulos -----------------------------------------------------------
    # Cada plantilla se rellena y se descarta si queda con marcador sin cubrir o
    # si se pasa del limite util. En Shorts el titulo se trunca, asi que el limite
    # efectivo es mucho menor que el de la descripcion.
    limite_titulo = 60 if plataforma == "youtube" else 80
    titulos = []
    for p in PLANTILLAS_TITULO:
        # Si el beneficio era relleno, no se puede prometer un resultado concreto.
        if "beneficio" in faltantes and "{beneficio}" in p["molde"]:
            continue
        if "dolor" in faltantes and "{dolor}" in p["molde"]:
            continue
        t = (p["molde"].replace("{beneficio}", _frase_corta(beneficio))
             .replace("{dolor}", _frase_corta(dolor, 5, 32))
             .replace("{tema}", _frase_corta(tema, 5, 32))
             .replace("{duracion}", str(dur)))
        if "{" in t or "}" in t:
            continue
        t = t[:limite_titulo].rstrip()
        if t and t not in [x["texto"] for x in titulos]:
            titulos.append({
                "texto": t, "id": p["id"], "por_que": p["por_que"],
                "caracteres": len(t),
                "evidencia": "restriccion de plataforma" if p["id"] == "especifico" else "heuristica",
            })
    titulos = titulos[:5]

    # ---- Descripcion -------------------------------------------------------
    gancho = _limpiar(clip.get("hook") or (titulos[0]["texto"] if titulos else tema))
    copy_base = _limpiar(clip.get("copy") or "")
    primera_linea = gancho[:110]
    cuerpo = copy_base or beneficio
    # Las primeras 1–2 lineas son lo unico visible antes del "ver mas": el gancho
    # va ahi por restriccion de interfaz, no por gusto.
    descripcion = (
        f"{primera_linea}\n\n"
        f"{cuerpo[:300]}\n\n"
        f"{'#' + marca.replace(' ', '') if marca else ''} #ad"
    ).strip()
    if len(descripcion) > aj["max_copy_chars"]:
        descripcion = descripcion[: aj["max_copy_chars"] - 1] + "…"

    # ---- Hashtags por nivel ------------------------------------------------
    # Tres niveles: nicho (alcance chico, relevancia alta), medio, amplio.
    # El conteo respeta el ajuste por plataforma; mas hashtags no es mas alcance
    # y en Instagram el exceso se lee como spam.
    nicho = [h for h in [f"#{_slug(categoria, minusculas=False)}" if categoria else "",
                         f"#{_slug(marca, minusculas=False)}" if marca else ""] if len(h) >= 4]
    _slug_tema = _slug(tema)
    medio = [f"#{_slug_tema}"] if len(_slug_tema) >= 4 else []
    amplio = ["#parati" if plataforma != "youtube" else "#shorts"]
    n = aj["hashtags"]
    hashtags = {"nicho": nicho[:2], "medio": medio[:1], "amplio": amplio[:1]}
    total = sum(len(v) for v in hashtags.values())
    if total > n:
        hashtags["amplio"] = []
        if sum(len(v) for v in hashtags.values()) > n:
            hashtags["medio"] = []

    # ---- Miniatura ---------------------------------------------------------
    miniatura = {
        "instruccion": ("Frame del segundo 1–2 con el rostro o el producto a cuadro completo, "
                        "subtitulo quemado de 4–6 palabras arriba, contraste alto."),
        "por_que": ("Restriccion de plataforma: en el feed de perfil y en buscador la miniatura "
                    "es lo unico que se ve. En el For You pesa menos que el primer frame."),
        "evitar": ["Logo de la herramienta de edicion", "Texto de mas de 6 palabras",
                   "Frame en negro o desenfocado"],
    }

    # ---- Primer comentario -------------------------------------------------
    # En TikTok el primer comentario propio se indexa y aporta contexto sin
    # consumir caracteres del caption.
    primer_comentario = {
        "texto": f"Pregunta para los que ya lo probaron: {dolor} ¿les paso? #ad",
        "por_que": ("Heuristica: una pregunta propia genera respuestas tempranas, y los "
                    "comentarios en la primera hora son senal de distribucion."),
        "evidencia": "heuristica",
    }

    # ---- Checklist ---------------------------------------------------------
    checklist = [
        {"item": "Divulgacion #ad presente en la descripcion", "obligatorio": True,
         "evidencia": "restriccion legal: contenido pagado sin divulgar es sancionable"},
        {"item": "Subtitulos quemados (la mayoria ve sin audio)", "obligatorio": True,
         "evidencia": "restriccion de plataforma"},
        {"item": "Sin marca de agua de otra app", "obligatorio": True,
         "evidencia": "restriccion de plataforma: TikTok reduce alcance con marcas ajenas"},
        {"item": f"Duracion entre {aj['duracion_objetivo'][0]}s y {aj['duracion_objetivo'][1]}s",
         "obligatorio": True, "evidencia": "restriccion de plataforma"},
        {"item": "Material 100% de los assets licenciados de la campana", "obligatorio": True,
         "evidencia": "contractual: material ajeno = duplicado filtrado y pago perdido"},
        {"item": "Audio con licencia comercial (cuenta Business)", "obligatorio": False,
         "evidencia": "restriccion de plataforma"},
        {"item": "Gancho distinto al de tus otros clips de esta campana", "obligatorio": True,
         "evidencia": "contractual: los duplicados se filtran y no pagan"},
    ]

    # ---- Riesgos detectados en el texto actual -----------------------------
    texto_completo = f"{descripcion} {gancho} {copy_base}".lower()
    riesgos = detectar_fragiles(texto_completo)

    kit = {
        "clip_id": clip.get("id", ""),
        "plataforma": plataforma,
        "marca": marca,
        "categoria": categoria,
        "duracion_seg": dur,
        "titulos_propuestos": titulos,
        "titulo_recomendado": titulos[0]["texto"] if titulos else tema,
        "descripcion": descripcion,
        "descripcion_caracteres": len(descripcion),
        "descripcion_limite": aj["max_copy_chars"],
        "hashtags": hashtags,
        "hashtags_lista": [h for v in hashtags.values() for h in v],
        "hashtags_max_recomendado": n,
        "gancho_visual": gancho,
        "miniatura": miniatura,
        "primer_comentario": primer_comentario,
        "ventanas_publicacion": VENTANAS.get(plataforma, VENTANAS["tiktok"]),
        "checklist": checklist,
        "riesgos_detectados": riesgos,
        "insumos_insuficientes": faltantes,
        "requiere_revision": bool(faltantes),
        "_como_leer_esto": ("Lo marcado 'restriccion de plataforma' es una regla dura. "
                            "Lo marcado 'heuristica' es un patron que suele funcionar, no una "
                            "garantia. Ningun kit hace viral un clip: la retencion del video "
                            "manda, y eso se decide en la edicion."),
    }
    return kit


def enriquecer_con_llm(kit: dict, llm_cfg: dict | None = None) -> dict:
    """
    Opcional: pide a un LLM 3 titulos mas y los agrega.

    Se mantiene opcional a proposito: el kit base es deterministico y auditable.
    Si el LLM falla o no esta configurado, el kit sigue siendo util tal cual.
    Nunca se permite que el LLM invente cifras: se le pide explicitamente que no.
    """
    if not llm_cfg or not llm_cfg.get("api_key"):
        return kit
    import urllib.request

    prompt = (
        f"Plataforma: {kit['plataforma']}. Marca: {kit['marca']}. Categoria: {kit['categoria']}.\n"
        f"Duracion: {kit['duracion_seg']}s. Gancho actual: {kit['gancho_visual']}\n\n"
        "Escribe 3 titulos alternativos de maximo 80 caracteres, en espanol, sin signos de "
        "exclamacion multiples. REGLAS DURAS: no inventes cifras ni porcentajes, no prometas "
        "resultados garantizados, incluye la divulgacion si corresponde. Devuelve solo un JSON "
        'con {"titulos": ["...", "...", "..."]}.'
    )
    cuerpo = json.dumps({
        "model": llm_cfg.get("modelo", "gpt-4o-mini"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }).encode()
    req = urllib.request.Request(
        llm_cfg.get("url", "https://api.openai.com/v1/chat/completions"), data=cuerpo,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {llm_cfg['api_key']}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        contenido = resp["choices"][0]["message"]["content"]
        inicio, fin = contenido.find("{"), contenido.rfind("}")
        datos = json.loads(contenido[inicio:fin + 1])
        extra = [{"texto": t[:80], "id": "llm", "por_que": "Generado por LLM (revisalo antes de usar).",
                  "caracteres": len(t[:80]), "evidencia": "llm"}
                 for t in datos.get("titulos", []) if _limpiar(t)]
        kit["titulos_propuestos"] = (kit["titulos_propuestos"] + extra)[:8]
    except Exception as e:  # el kit base sigue siendo valido
        kit["llm_error"] = str(e)[:200]
    return kit


def kit_a_texto(kit: dict) -> str:
    """Version plana del kit, para la notificacion de Telegram y el CLI."""
    l = [f"🎬 KIT DE PUBLICACION — {kit['plataforma']} / {kit['marca'] or kit['categoria']}",
         f"Duración: {kit['duracion_seg']}s · {len(kit['titulos_propuestos'])} títulos propuestos",
         "", "📝 TÍTULOS PROPUESTOS"]
    for i, t in enumerate(kit["titulos_propuestos"], 1):
        l.append(f"  {i}. {t['texto']}  ({t['caracteres']} car.)")
        l.append(f"     ↳ {t['por_que']}")
    l += ["", f"📄 DESCRIPCIÓN ({kit['descripcion_caracteres']}/{kit['descripcion_limite']} car.)",
          kit["descripcion"],
          "", "🏷 HASHTAGS",
          f"  Nicho: {' '.join(kit['hashtags']['nicho']) or '—'}",
          f"  Medio: {' '.join(kit['hashtags']['medio']) or '—'}",
          f"  Amplio: {' '.join(kit['hashtags']['amplio']) or '—'}",
          "", "🖼 MINIATURA", f"  {kit['miniatura']['instruccion']}",
          "", "💬 PRIMER COMENTARIO", f"  {kit['primer_comentario']['texto']}",
          "", "⏰ VENTANAS DE PUBLICACIÓN (hora local)"]
    for v in kit["ventanas_publicacion"]:
        l.append(f"  • {v['ventana']} — {v['por_que']}")
    l += ["", "✅ CHECKLIST ANTES DE PUBLICAR"]
    for c in kit["checklist"]:
        l.append(f"  [{' ' if not c['obligatorio'] else 'X'}] {c['item']}")
    if kit.get("insumos_insuficientes"):
        l += ["", "⚠️ INSUMOS INSUFICIENTES: " + ", ".join(kit["insumos_insuficientes"]) +
              " — completa esos campos del brief de la campaña antes de usar estos títulos."]
    if kit["riesgos_detectados"]:
        l += ["", "⚠️ RIESGOS DETECTADOS EN EL TEXTO"]
        for r in kit["riesgos_detectados"]:
            l.append(f"  • '{r['frase']}': {r['razon']}")
    l += ["", kit["_como_leer_esto"]]
    return "\n".join(l)
