"""
Calculo de pagos y reportes de rendimiento.

Modelo real de pago en campanas tipo Whop Content Rewards:
  * se paga por cada 1,000 VISTAS VERIFICADAS (no vistas totales),
  * hay un umbral minimo de vistas por clip (debajo = $0),
  * hay un tope (cap) por clip, comunmente $100–$500,
  * la plataforma cobra comision (Whop ~9%),
  * cuando el presupuesto de la campana se agota, las vistas dejan de pagar aunque
    el video siga acumulando reproducciones.

Cualquier herramienta que te muestre "views x CPM" sin estos cuatro filtros
te esta mostrando un numero que nunca vas a cobrar.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import db

COMISION_PLATAFORMA = {
    "whop": 0.09,
    "ssemble": 0.0,
    "discord": 0.0,
    "default": 0.09,
}

# Ventana tipica antes de que las vistas se consideren verificadas y pagables.
DIAS_VERIFICACION = 5


@dataclass
class Liquidacion:
    post_id: str
    views: int
    views_verificadas: int
    cpm_usd: float
    bruto_usd: float
    comision_usd: float
    neto_usd: float
    cap_aplicado: bool
    motivo_cero: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def liquidar_post(post: dict, campana: dict, dias_desde_publicacion: float) -> Liquidacion:
    cpm = campana.get("cpm_usd") or 0.0
    plataforma = (campana.get("plataforma") or "default").lower()
    comision_pct = COMISION_PLATAFORMA.get(plataforma, COMISION_PLATAFORMA["default"])

    views = int(post.get("views") or 0)
    verif = post.get("views_verif")
    verif = int(verif) if verif is not None else (views if dias_desde_publicacion >= DIAS_VERIFICACION else 0)

    bruto = 0.0
    motivo = None

    if cpm <= 0:
        motivo = "Campana sin CPM registrado"
    elif dias_desde_publicacion < DIAS_VERIFICACION:
        motivo = f"Dentro de la ventana de verificacion ({DIAS_VERIFICACION} dias): vistas aun no pagables"
    elif verif < (campana.get("min_views") or 0):
        motivo = f"{verif} vistas < umbral minimo de {campana.get('min_views')}: el clip paga $0"
    elif (campana.get("presupuesto_rest") or 0) <= 0 and campana.get("presupuesto_total"):
        motivo = "Presupuesto de la campana agotado: las vistas adicionales no pagan"
    else:
        bruto = verif / 1000.0 * cpm

    cap = campana.get("cap_por_clip_usd")
    cap_aplicado = False
    if cap and bruto > cap:
        bruto = float(cap)
        cap_aplicado = True

    comision = bruto * comision_pct
    return Liquidacion(
        post_id=post.get("id", ""), views=views, views_verificadas=verif, cpm_usd=cpm,
        bruto_usd=round(bruto, 2), comision_usd=round(comision, 2),
        neto_usd=round(bruto - comision, 2), cap_aplicado=cap_aplicado, motivo_cero=motivo,
    )


def registrar_vistas(post_id: str, views: int, views_verif: int | None = None, fuente: str = "manual") -> None:
    with db.sesion() as c:
        c.execute("INSERT INTO view_events (post_id, visto_en, views, views_verif, fuente) VALUES (?,?,?,?,?)",
                  (post_id, db.ahora(), views, views_verif, fuente))
        c.execute("UPDATE posts SET views = ?, views_verif = COALESCE(?, views_verif) WHERE id = ?",
                  (views, views_verif, post_id))
        db.log(c, "vistas_registradas", {"post_id": post_id, "views": views})


def registrar_pago(plataforma: str, monto_usd: float, fecha: str, metodo: str = "", comision: float = 0.0) -> None:
    with db.sesion() as c:
        c.execute("INSERT INTO payouts (plataforma, monto_usd, fecha, metodo, comision) VALUES (?,?,?,?,?)",
                  (plataforma, monto_usd, fecha, metodo, comision))
        db.log(c, "pago_registrado", {"plataforma": plataforma, "monto": monto_usd})


def _dias(iso: str | None) -> float:
    if not iso:
        return 0.0
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 86400.0)


def reporte(desde_dias: int = 30) -> dict:
    """Estado financiero consolidado: lo unico que el operador necesita leer."""
    with db.sesion() as c:
        posts = db.filas(c.execute(
            "SELECT p.*, c.cpm_usd, c.min_views, c.cap_por_clip_usd, c.presupuesto_rest, "
            "c.presupuesto_total, c.plataforma AS camp_plataforma "
            "FROM posts p JOIN campaigns c ON c.id = p.campaign_id"))
        cobrado = db.uno(c, "SELECT COALESCE(SUM(monto_usd),0) AS total FROM payouts") or {"total": 0}
        n_campanas = db.uno(c, "SELECT COUNT(*) AS n FROM campaigns") or {"n": 0}
        n_clips = db.uno(c, "SELECT COUNT(*) AS n FROM clips") or {"n": 0}
        bloqueados = db.uno(c, "SELECT COUNT(*) AS n FROM clips WHERE compliance_ok = 0 AND estado != 'idea'") or {"n": 0}

    lines = []
    total_neto = 0.0
    total_views = 0
    pendientes_verif = 0
    por_motivo: dict[str, int] = {}
    por_campana: dict[str, dict] = {}

    for p in posts:
        liq = liquidar_post(p, {
            "cpm_usd": p["cpm_usd"], "min_views": p["min_views"], "cap_por_clip_usd": p["cap_por_clip_usd"],
            "presupuesto_rest": p["presupuesto_rest"], "presupuesto_total": p["presupuesto_total"],
            "plataforma": p["camp_plataforma"],
        }, _dias(p.get("publicado_en")))
        total_neto += liq.neto_usd
        total_views += liq.views
        if liq.motivo_cero:
            clave = ("ventana de verificacion" if "verificacion" in liq.motivo_cero
                     else "umbral minimo" if "umbral" in liq.motivo_cero
                     else "pool agotado" if "agotado" in liq.motivo_cero
                     else "otro")
            por_motivo[clave] = por_motivo.get(clave, 0) + 1
            if clave == "ventana de verificacion":
                pendientes_verif += 1
        k = p["campaign_id"]
        g = por_campana.setdefault(k, {"views": 0, "neto_usd": 0.0, "posts": 0})
        g["views"] += liq.views
        g["neto_usd"] += liq.neto_usd
        g["posts"] += 1
        lines.append({
            "post": p["id"], "plataforma": p["plataforma"], "views": liq.views,
            "verificadas": liq.views_verificadas, "neto_usd": liq.neto_usd,
            "nota": liq.motivo_cero or ("cap aplicado" if liq.cap_aplicado else "ok"),
        })

    mejores = sorted(por_campana.items(), key=lambda kv: kv[1]["neto_usd"], reverse=True)[:5]
    return {
        "periodo_dias": desde_dias,
        "campanas_rastreadas": n_campanas["n"],
        "clips_producidos": n_clips["n"],
        "clips_bloqueados_por_compliance": bloqueados["n"],
        "posts": len(posts),
        "views_totales": total_views,
        "ganancia_estimada_neta_usd": round(total_neto, 2),
        "posts_aun_en_verificacion": pendientes_verif,
        "posts_que_no_pagan_por": por_motivo,
        "cobrado_real_usd": round(cobrado["total"], 2),
        "diferencia_estimado_vs_cobrado_usd": round(cobrado["total"] - total_neto, 2),
        "mejores_campanas": [{"id": k, **v, "neto_usd": round(v["neto_usd"], 2)} for k, v in mejores],
        "detalle_posts": lines,
        "_nota": ("'ganancia_estimada' es lo que deberias cobrar segun el modelo, y SOLO cuenta "
                  "vistas ya verificadas: los posts dentro de la ventana de ~5 dias aparecen en "
                  "cero aunque acumulen vistas. "
                  "'cobrado_real' es lo que la plataforma efectivamente deposito. "
                  "Si la diferencia es grande y negativa, revisa rechazos y vistas filtradas."),
    }


def resumen_texto(desde_dias: int = 30) -> str:
    r = reporte(desde_dias)
    l = [
        "=" * 62,
        f"  REPORTE DE RENDIMIENTO — ultimos {r['periodo_dias']} dias",
        "=" * 62,
        f"  Campanas rastreadas ........ {r['campanas_rastreadas']}",
        f"  Clips producidos ........... {r['clips_producidos']}",
        f"  Bloqueados por compliance .. {r['clips_bloqueados_por_compliance']}",
        f"  Posts publicados ........... {r['posts']}",
        f"  Vistas totales ............. {r['views_totales']:,}",
        "-" * 62,
        f"  Ganancia estimada neta ..... ${r['ganancia_estimada_neta_usd']:.2f} USD",
        *(f"  · {n} post(s) sin pagar por: {m}" for m, n in r["posts_que_no_pagan_por"].items()),
        f"  Cobrado realmente .......... ${r['cobrado_real_usd']:.2f} USD",
        f"  Diferencia ................. ${r['diferencia_estimado_vs_cobrado_usd']:+.2f} USD",
        "-" * 62,
    ]
    if r["mejores_campanas"]:
        l.append("  Mejores campanas:")
        for m in r["mejores_campanas"]:
            l.append(f"    · {m['id']}  ${m['neto_usd']:.2f}  ({m['views']:,} views, {m['posts']} posts)")
    else:
        l.append("  Sin datos de campanas todavia.")
    l.append("=" * 62)
    return "\n".join(l)
