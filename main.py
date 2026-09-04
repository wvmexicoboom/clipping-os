#!/usr/bin/env python3
"""CLI de Clipping OS. Corre `python main.py -h` para ver todo."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipper import compliance, db, discovery, earnings, publish  # noqa: E402


def _reglas(cfg: dict) -> dict:
    """Reglas efectivas = protecciones por defecto + lo que agregue config.json."""
    return compliance.reglas_efectivas(cfg.get("reglas_duras", {}))


def cargar_config() -> dict:
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(ruta):
        return {}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def cmd_init(_a):
    print("Base creada en:", db.inicializar())


def cmd_importar(a):
    db.inicializar()
    r = discovery.importar_csv(a.csv, a.plataforma, _reglas(cargar_config()))
    print(f"Importadas: {r['importadas']} · errores: {r['errores']}")


def cmd_ranking(a):
    db.inicializar()
    cfg = cargar_config()
    filas = discovery.mejores_campanas(a.limite, a.minimo,
                                       aplicar_compuerta=not a.todas,
                                       reglas=_reglas(cfg))
    if not filas:
        print("Sin campanas. Usa: python main.py importar --csv ejemplo_campanas.csv")
        return
    print(f"{'PUNTAJE':>8}  {'CPM':>6}  {'POOL REST':>10}  {'ID':<14} MARCA")
    print("-" * 72)
    for c in filas:
        print(f"{c['puntaje']:>8.2f}  ${c['cpm_usd'] or 0:>5.2f}  "
              f"{(c['presupuesto_rest'] or 0):>10,.0f}  {c['id']:<14} {c.get('marca') or '—'}")
    if a.verificar:
        print("\nVeredicto de la mejor campana:")
        v = compliance.revisar_campana(filas[0], _reglas(cfg))
        for b in v.bloqueos:
            print("  BLOQUEO:", b)
        for w in v.avisos:
            print("  aviso  :", w)
        print("  =>", "APROBADA para producir" if v.ok else "NO entrar")


def cmd_aprobar(a):
    db.inicializar()
    cfg = cargar_config()
    with db.sesion() as c:
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id = ?", (a.id,))
        if not camp:
            sys.exit(f"No existe la campana {a.id}")
        v = compliance.revisar_campana(camp, _reglas(cfg))
        for b in v.bloqueos:
            print("BLOQUEO:", b)
        for w in v.avisos:
            print("aviso  :", w)
        if not v.ok:
            c.execute("UPDATE campaigns SET estado='descartada' WHERE id=?", (a.id,))
            db.log(c, "campana_descartada", a.id)
            sys.exit("Campana descartada: no pasa la compuerta.")
        if a.forzar:
            print("AVISO: forzado por el operador a pesar de los avisos.")
        c.execute("UPDATE campaigns SET estado='aprobada' WHERE id=?", (a.id,))
        db.log(c, "campana_aprobada", a.id)
    print(f"Campana {a.id} aprobada para produccion.")


def cmd_asset(a):
    db.inicializar()
    aid = discovery.registrar_asset(a.campana, a.tipo, a.ruta, a.licencia, a.duracion, a.prueba)
    print("Asset registrado:", aid, f"(licencia={a.licencia})")


def cmd_brief(a):
    db.inicializar()
    from clipper import clip_spec
    with db.sesion() as c:
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id = ?", (a.campana,))
    if not camp:
        sys.exit(f"No existe la campana {a.campana}")
    if camp.get("estado") != "aprobada":
        sys.exit(f"La campana esta en estado '{camp.get('estado')}'. Primero: python main.py aprobar --id {a.campana}")

    aid = a.asset
    if not aid:
        with db.sesion() as c:
            filas = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=? AND licencia IN "
                                       "('campana','propia','cc0','licenciada')", (a.campana,)))
        if not filas:
            sys.exit("No hay assets licenciados registrados. Usa: python main.py asset --campana ... --licencia campana")
        aid = filas[0]["id"]

    cfg = cargar_config()
    cid = clip_spec.nuevo_id_clip(a.campana, aid, a.plataforma, a.tema)
    brief = clip_spec.brief_desde_llm(camp, a.plataforma, a.tema, a.dolor, a.beneficio,
                                      cfg.get("llm"), clip_id=cid, asset_id=aid,
                                      objecion=a.objecion)
    assert brief.clip_id == cid, f"id inconsistente: {brief.clip_id} != {cid}"

    with db.sesion() as c:
        c.execute("""INSERT INTO clips (id, campaign_id, titulo, hook, copy, duracion_seg,
                     asset_origen, estado, creado_en)
                     VALUES (?,?,?,?,?,?,?,'listo',?)
                     ON CONFLICT(id) DO UPDATE SET copy=excluded.copy, hook=excluded.hook""",
                  (cid, a.campana, brief.gancho["plantilla"], brief.gancho["plantilla"],
                   brief.copy, brief.duracion_seg, aid, db.ahora()))
    ruta = os.path.join(publish.RUTA_SALIDA, f"brief_{cid}.md")
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(brief.markdown())
    print("Brief generado:", ruta)
    print("Clip id:", cid)
    print("\n" + brief.markdown())


def cmd_encolar(a):
    db.inicializar()
    cfg = cargar_config()
    with db.sesion() as c:
        clip = db.uno(c, "SELECT * FROM clips WHERE id = ?", (a.clip,))
        if not clip:
            sys.exit(f"No existe el clip {a.clip}")
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id = ?", (clip["campaign_id"],))
        assets = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=?", (camp["id"],)))
    try:
        r = publish.encolar_clip(clip, camp, assets, a.plataforma, a.cuenta, _reglas(cfg), a.modo)
    except publish.ErrorPublicacion as e:
        sys.exit(f"\n✗ {e}\n  (El clip quedo marcado como 'bloqueado' en el panel.)")
    print("✓ En cola:", r["post_id"], f"(modo={r['modo']})")
    for w in r["compliance"]["avisos"]:
        print("  aviso:", w)


def cmd_cola(_a):
    db.inicializar()
    ruta = publish.exportar_cola()
    cola = publish.posts_en_cola()
    print(f"{len(cola)} post(s) en cola → {ruta}")
    for p in cola:
        print(f"  · {p['id']}  {p['plataforma']:<9} {p['cuenta'] or ''}")


def cmd_publicado(a):
    db.inicializar()
    publish.marcar_publicado(a.post_id, a.url, a.via)
    print("✓ Registrado como publicado:", a.url)
    print("  Recuerda: envia esa URL a la campana. Sin envio no hay pago.")


def cmd_vistas(a):
    db.inicializar()
    earnings.registrar_vistas(a.post_id, a.views, a.verificadas, a.fuente)
    print(f"✓ Vistas actualizadas para {a.post_id}: {a.views:,}")


def cmd_pago(a):
    db.inicializar()
    earnings.registrar_pago(a.plataforma, a.monto, a.fecha or db.ahora()[:10], a.metodo)
    print(f"✓ Pago registrado: ${a.monto:.2f} desde {a.plataforma}")


def cmd_reporte(a):
    db.inicializar()
    print(earnings.resumen_texto(a.dias))
    if a.json:
        print(json.dumps(earnings.reporte(a.dias), ensure_ascii=False, indent=2))


def cmd_verificar(a):
    db.inicializar()
    cfg = cargar_config()
    with db.sesion() as c:
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id = ?", (a.campana,))
        clip = db.uno(c, "SELECT * FROM clips WHERE id = ?", (a.clip,))
        assets = db.filas(c.execute("SELECT * FROM assets WHERE campaign_id=?", (camp["id"],)))
    v = compliance.revisar_clip(clip, camp, assets, _reglas(cfg))
    print("APROBADO" if v.ok else "BLOQUEADO")
    for b in v.bloqueos:
        print("  BLOQUEO:", b)
    for w in v.avisos:
        print("  aviso  :", w)
    sys.exit(0 if v.ok else 1)


def _proveedor(nombre):
    from clipper import providers
    return providers.obtener(nombre, cargar_config())


def cmd_proveedores(_a):
    from clipper import providers
    print(f"{'PROVEEDOR':<11} {'MODO':<9} {'API':<5} {'CREDENCIAL':<12} NOTA")
    print("-" * 100)
    for d in providers.listar(cargar_config()):
        if not d["tiene_api"]:
            cred = "no necesita"
        else:
            cred = "lista" if d["configurado"] else "FALTA"
        print(f"{d['nombre']:<11} {d['modo']:<9} {'si' if d['tiene_api'] else 'no':<5} "
              f"{cred:<12} {d['nota_acceso'][:58]}")
    print("\nNingun video vuelve a la cola sin pasar otra vez por compliance,")
    print("incluido el detector de marca de agua.")


def cmd_proveedor_enviar(a):
    db.inicializar()
    from clipper import providers
    prov = _proveedor(a.proveedor)
    with db.sesion() as c:
        asset = db.uno(c, "SELECT * FROM assets WHERE id=?", (a.asset,))
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id=?", (asset["campaign_id"],)) if asset else None
    if not asset:
        sys.exit(f"No existe el asset {a.asset}")
    v = compliance.revisar_campana(camp, _reglas(cargar_config()))
    if not v.ok:
        sys.exit("La campana no pasa la compuerta: " + " | ".join(v.bloqueos))

    from clipper import clip_spec
    brief = clip_spec.generar_brief(f"cl_{a.asset}", camp, a.plataforma, a.tema, a.dolor, a.beneficio)
    try:
        r = prov.enviar(asset, brief, camp)
    except providers.ErrorProveedor as e:
        sys.exit(f"\n✗ {e}")
    jid = providers.registrar_trabajo(prov.nombre, camp["id"], a.asset, r.get("project_id", ""),
                                      "enviado", r.get("respuesta"))
    print(f"✓ Trabajo #{jid} enviado a {prov.nombre}: proyecto {r.get('project_id')}")


def cmd_proveedor_estado(a):
    db.inicializar()
    from clipper import providers
    trabajos = providers.trabajos(pendiente=not a.todo)
    if not trabajos:
        print("Sin trabajos registrados.")
        return
    for t in trabajos:
        if t["estado"] in ("listo", "fallido"):
            print(f"  #{t['id']} {t['proveedor']:<10} {t['estado']:<11} ref={t['referencia']}")
            continue
        prov = _proveedor(t["proveedor"])
        try:
            r = prov.estado(t)
        except providers.ErrorProveedor as e:
            print(f"  #{t['id']} {t['proveedor']:<10} error: {str(e)[:70]}")
            continue
        nuevo = "listo" if r.get("listo") else "procesando"
        providers.actualizar_trabajo(t["id"], nuevo, r.get("n_clips"), r)
        print(f"  #{t['id']} {t['proveedor']:<10} {nuevo:<11} clips={r.get('n_clips')} ref={t['referencia']}")


def cmd_proveedor_preparar(a):
    db.inicializar()
    from clipper import providers, clip_spec
    prov = _proveedor(a.proveedor)
    with db.sesion() as c:
        asset = db.uno(c, "SELECT * FROM assets WHERE id=?", (a.asset,))
        if not asset:
            sys.exit(f"No existe el asset {a.asset}")
        camp = db.uno(c, "SELECT * FROM campaigns WHERE id=?", (asset["campaign_id"],))
    v = compliance.revisar_campana(camp, _reglas(cargar_config()))
    if not v.ok:
        sys.exit("La campana no pasa la compuerta: " + " | ".join(v.bloqueos))

    cid = clip_spec.nuevo_id_clip(camp["id"], a.asset, a.plataforma, a.tema)
    brief = clip_spec.generar_brief(cid, camp, a.plataforma, a.tema, a.dolor, a.beneficio,
                                    objecion=a.objecion)
    with db.sesion() as c:
        c.execute("""INSERT INTO clips (id, campaign_id, titulo, hook, copy, duracion_seg,
                     asset_origen, estado, creado_en)
                     VALUES (?,?,?,?,?,?,?,'en_produccion',?)
                     ON CONFLICT(id) DO UPDATE SET copy=excluded.copy""",
                  (cid, camp["id"], brief.gancho["plantilla"], brief.gancho["plantilla"],
                   brief.copy, brief.duracion_seg, a.asset, db.ahora()))
    destino = os.path.join(publish.RUTA_SALIDA, f"trabajo_{prov.nombre}_{cid}")
    ruta = prov.preparar_carpeta(asset, brief, camp, destino)
    print(f"✓ Carpeta de trabajo lista: {ruta}")
    print(f"  clip id: {cid}")
    print("\n" + prov.instrucciones(asset, brief, camp))


def cmd_render(a):
    db.inicializar()
    from clipper import providers
    try:
        r = providers.registrar_render(a.clip, a.archivo, a.proveedor, a.forzar)
    except providers.ErrorProveedor as e:
        sys.exit(f"\n✗ {e}")
    print(f"✓ Render registrado para {r['clip_id']}: {r['archivo']}")
    print(f"  pixeles tipo logo en esquina: {r['fraccion_tipo_logo']:.2%}")
    if r["aviso"]:
        print("  AVISO:", r["aviso"])
    print("  Siguiente paso: python main.py encolar --clip " + r["clip_id"])


def _passphrase(a) -> str | None:
    import getpass
    if getattr(a, "sin_cifrar", False):
        return None
    p = os.environ.get("CLIPPER_PASSPHRASE") or getattr(a, "passphrase", None)
    if p:
        return p
    if not sys.stdin.isatty():
        return None
    return getpass.getpass("Passphrase para cifrar (Enter = archivo plano chmod 600): ") or None


def cmd_secretos(a):
    from clipper import secrets
    inv = secrets.inventario(_passphrase(a))
    for p in inv:
        print(f"\n{p['proveedor'].upper()}")
        print(f"  login      {p.get('login','-')}")
        print(f"  api key en {p.get('api_key_en','-')}")
        if p["credenciales"]:
            for c in p["credenciales"]:
                marca = "OK " if c["guardado"] else "-- "
                print(f"  {marca}{c['campo']:<15} {c['nivel']:<26} {c['muestra']}")
        else:
            print("  (no usa credenciales: modo asistido)")
        print(f"  requiere   {p.get('requiere','-')}")


def cmd_secretos_set(a):
    db.inicializar()
    from clipper import secrets
    import getpass
    valor = a.valor
    if valor == "-":
        valor = getpass.getpass(f"Pega la API key de {a.proveedor} (no se muestra): ")
    if not valor:
        sys.exit("Valor vacio: no se guardo nada.")
    try:
        nivel = secrets.guardar(a.proveedor, a.campo, valor, _passphrase(a))
    except (ValueError, RuntimeError) as e:
        sys.exit(f"✗ {e}")
    print(f"✓ Guardado {a.proveedor}.{a.campo} → {nivel}")
    if "NO es cifrado" in nivel:
        print("  Sugerencia: `pip install cryptography` y repite con passphrase para cifrarlo,")
        print("  o exportalo como variable de entorno y borra el archivo.")
    with db.sesion() as c:
        db.log(c, "secreto_guardado", {"proveedor": a.proveedor, "campo": a.campo, "nivel": nivel})


def cmd_secretos_del(a):
    from clipper import secrets
    ok = secrets.borrar(a.proveedor, a.campo, _passphrase(a))
    print("✓ Borrado." if ok else "No estaba guardado.")


def cmd_auto(a):
    db.inicializar()
    from clipper import auto
    cfg = cargar_config()
    if a.vigilar:
        n = auto.vigilar(cfg, a.intervalo, a.ciclos, a.cuenta)
        print(f"\n{n} ciclo(s) completados.")
        return
    r1 = auto.cosechar(cfg, a.cuenta, a.dry_run)
    r2 = auto.lote(cfg, a.max, a.plataforma, a.cuenta, a.proveedor, a.dry_run)
    print(r1.resumen()); print(r2.resumen())


def cmd_avisar(a):
    db.inicializar()
    from clipper import notify
    cfg = cargar_config()
    res = notify.avisar_todos(cfg, url_panel=a.url_panel)
    if not res:
        print("No hay clips pendientes de autorizacion.")
        return
    for r in res:
        marca = "✓" if r["ok"] else "✗"
        print(f"  {marca} {r['post_id']} por {r['canal']}" + ("" if r["ok"] else f" — {r['error'][:80]}"))


def cmd_pendientes(_a):
    db.inicializar()
    from clipper import notify
    rows = notify.pendientes()
    if not rows:
        print("Nada pendiente de autorizacion.")
        return
    for r in rows:
        print(f"  {r['post_id']:<34} {r['plataforma']:<9} {r['cuenta'] or '':<14} desde {r['creado_en']}")
        print(f"     copy: {(r['copy'] or '')[:70]}")


def cmd_autorizar(a):
    db.inicializar()
    from clipper import notify
    try:
        r = notify.autorizar_por_token(a.token, not a.rechazar)
    except notify.ErrorNotificacion as e:
        sys.exit(f"✗ {e}")
    print(f"✓ {r['post_id']} → {r['estado']}")
    if r["estado"] == "aprobado":
        print("  Quedo en la cola de publicacion.")
    else:
        print("  Descartado. No se publicara.")


def cmd_kit(a):
    """Kit de publicacion de un clip: titulos, descripcion, hashtags, checklist."""
    db.inicializar()
    from clipper import viral
    with db.sesion() as c:
        clip = db.uno(c, "SELECT * FROM clips WHERE id=?", (a.clip,))
        if not clip:
            sys.exit(f"✗ No existe el clip '{a.clip}'.")
        campana = db.uno(c, "SELECT * FROM campaigns WHERE id=?", (clip["campaign_id"],)) or {}
    llm = None
    if a.llm:
        from clipper.secrets import obtener
        llm = {"api_key": obtener("llm", "api_key"), "url": obtener("llm", "url"),
               "modelo": obtener("llm", "modelo")}
    kit = viral.generar_kit(clip, campana, a.plataforma)
    if a.llm:
        kit = viral.enriquecer_con_llm(kit, llm)
    if a.json:
        print(json.dumps(kit, ensure_ascii=False, indent=2))
    else:
        print(viral.kit_a_texto(kit))
    if a.guardar:
        with open(a.guardar, "w", encoding="utf-8") as f:
            f.write(viral.kit_a_texto(kit) if not a.json
                    else json.dumps(kit, ensure_ascii=False, indent=2))
        print(f"\nGuardado en {a.guardar}")


def cmd_telegram_setup(a):
    """Comprueba el token del bot y descubre el chat_id sin que leas JSON."""
    db.inicializar()
    from clipper import notify
    from clipper.secrets import obtener, guardar
    if not obtener("telegram", "bot_token"):
        print("1. En Telegram abre @BotFather y escribe /newbot")
        print("2. Copia el token que te da (formato 123456:ABC-DEF...)")
        sys.exit("\nFalta el token. Guardalo con:\n"
                 "  python3 main.py secretos-set --proveedor telegram --campo bot_token")
    try:
        info = notify.verificar_telegram()
    except notify.ErrorNotificacion as e:
        sys.exit(f"✗ {e}")
    bot = info["bot"]
    print(f"✓ Token valido. Bot: @{bot.get('username')} ({bot.get('first_name')})")

    if info.get("chat_id"):
        print(f"✓ chat_id ya guardado: {info['chat_id']}")
        return
    if not info["chats"]:
        print("\nAun no encuentro tu chat. Haz esto:")
        print(f"  1. Abre @{bot.get('username')} en Telegram")
        print("  2. Toca INICIAR (o escribe /start)")
        print("  3. Vuelve a correr este comando")
        return
    print("\nConversaciones encontradas:")
    for cid, nombre in info["chats"].items():
        print(f"  {cid}  {nombre}")
    elegido = a.chat_id or (list(info["chats"])[0] if len(info["chats"]) == 1 else None)
    if not elegido:
        sys.exit("\nHay varias conversaciones. Indica cual con --chat-id <ID>")
    guardar("telegram", "chat_id", str(elegido), a.passphrase or "")
    print(f"\n✓ chat_id guardado: {elegido} ({info['chats'][elegido]})")
    print("  Prueba con: python3 main.py telegram-probar")


def cmd_telegram_probar(a):
    db.inicializar()
    from clipper import notify
    try:
        r = notify.enviar_prueba()
    except notify.ErrorNotificacion as e:
        sys.exit(f"✗ {e}")
    if not r.get("ok"):
        sys.exit(f"✗ Telegram no acepto el mensaje: {r.get('description')}")
    print(f"✓ Mensaje de prueba enviado (message_id {r['result']['message_id']}).")
    print("  Revisalo en Telegram. Si no llego, verifica el chat_id.")


def cmd_telegram_escuchar(a):
    """Recibe los toques de los botones Aprobar/Rechazar."""
    db.inicializar()
    from clipper import notify
    cfg = cargar_config()
    print("Escuchando Telegram. Toca Aprobar/Rechazar en tu telefono. (Ctrl+C para salir)")
    print("Mientras esto no corra, los botones no hacen nada: usa /autorizar en el panel.")
    try:
        while True:
            for h in notify.escuchar_telegram(cfg, max_ciclos=1):
                if h.get("error"):
                    print(f"  ✗ {h['error']}")
                else:
                    print(f"  {h['texto']}")
    except KeyboardInterrupt:
        print("\nDetenido.")


def cmd_panel(a):
    from clipper import app
    app.servir(a.host, a.puerto)


def main():
    p = argparse.ArgumentParser(prog="clipping-os", description="Sistema de operacion para clipping pagado")
    s = p.add_subparsers(dest="cmd", required=True)

    s.add_parser("init", help="crear la base de datos").set_defaults(f=cmd_init)

    x = s.add_parser("importar", help="importar campanas desde CSV")
    x.add_argument("--csv", required=True); x.add_argument("--plataforma", default="whop")
    x.set_defaults(f=cmd_importar)

    x = s.add_parser("ranking", help="campanas por valor esperado")
    x.add_argument("--limite", type=int, default=10); x.add_argument("--minimo", type=float, default=0.0)
    x.add_argument("--verificar", action="store_true")
    x.add_argument("--todas", action="store_true",
                   help="incluir campanas que la compuerta bloquearia")
    x.set_defaults(f=cmd_ranking)

    x = s.add_parser("aprobar", help="aprobar una campana para produccion")
    x.add_argument("--id", required=True); x.add_argument("--forzar", action="store_true")
    x.set_defaults(f=cmd_aprobar)

    x = s.add_parser("asset", help="registrar material licenciado de la campana")
    x.add_argument("--campana", required=True); x.add_argument("--tipo", default="vod")
    x.add_argument("--ruta", required=True); x.add_argument("--licencia", default="campana")
    x.add_argument("--duracion", type=float); x.add_argument("--prueba")
    x.set_defaults(f=cmd_asset)

    x = s.add_parser("brief", help="generar el brief de edicion de un clip")
    x.add_argument("--campana", required=True); x.add_argument("--plataforma", default="tiktok")
    x.add_argument("--tema", required=True); x.add_argument("--dolor", required=True)
    x.add_argument("--beneficio", required=True); x.add_argument("--asset")
    x.add_argument("--objecion", default="",
                   help="objecion tipica del publico; activa el gancho de tipo 'objecion'")
    x.set_defaults(f=cmd_brief)

    x = s.add_parser("verificar", help="pasar un clip por la compuerta de cumplimiento")
    x.add_argument("--campana", required=True); x.add_argument("--clip", required=True)
    x.set_defaults(f=cmd_verificar)

    x = s.add_parser("encolar", help="dejar un clip listo para publicar")
    x.add_argument("--clip", required=True); x.add_argument("--plataforma", default="tiktok")
    x.add_argument("--cuenta", default=""); x.add_argument("--modo", default="cola")
    x.set_defaults(f=cmd_encolar)

    s.add_parser("cola", help="ver/exportar la cola de publicacion").set_defaults(f=cmd_cola)

    x = s.add_parser("publicado", help="registrar que ya publicaste")
    x.add_argument("--post-id", required=True); x.add_argument("--url", required=True)
    x.add_argument("--via", default="manual")
    x.set_defaults(f=cmd_publicado)

    x = s.add_parser("vistas", help="registrar vistas de un post")
    x.add_argument("--post-id", required=True); x.add_argument("--views", type=int, required=True)
    x.add_argument("--verificadas", type=int); x.add_argument("--fuente", default="manual")
    x.set_defaults(f=cmd_vistas)

    x = s.add_parser("pago", help="registrar un pago recibido")
    x.add_argument("--plataforma", required=True); x.add_argument("--monto", type=float, required=True)
    x.add_argument("--fecha"); x.add_argument("--metodo", default="")
    x.set_defaults(f=cmd_pago)

    x = s.add_parser("reporte", help="reporte de rendimiento")
    x.add_argument("--dias", type=int, default=30); x.add_argument("--json", action="store_true")
    x.set_defaults(f=cmd_reporte)

    s.add_parser("proveedores", help="listar herramientas de video conectables").set_defaults(f=cmd_proveedores)

    x = s.add_parser("secretos", help="ver credenciales guardadas y donde se obtiene cada una")
    x.add_argument("--passphrase"); x.add_argument("--sin-cifrar", action="store_true")
    x.set_defaults(f=cmd_secretos)

    x = s.add_parser("secretos-set", help="guardar una API key (una sola vez)")
    x.add_argument("--proveedor", required=True); x.add_argument("--campo", default="api_key")
    x.add_argument("--valor", default="-", help="usa '-' (default) para pegarla sin que se vea")
    x.add_argument("--passphrase"); x.add_argument("--sin-cifrar", action="store_true")
    x.set_defaults(f=cmd_secretos_set)

    x = s.add_parser("secretos-del", help="borrar una credencial guardada")
    x.add_argument("--proveedor", required=True); x.add_argument("--campo", default="api_key")
    x.add_argument("--passphrase"); x.add_argument("--sin-cifrar", action="store_true")
    x.set_defaults(f=cmd_secretos_del)

    x = s.add_parser("auto", help="ciclo autonomo de produccion (no publica)")
    x.add_argument("--max", type=int, default=3); x.add_argument("--plataforma", default="tiktok")
    x.add_argument("--cuenta", default="principal"); x.add_argument("--proveedor")
    x.add_argument("--dry-run", action="store_true", help="genera briefs sin enviar nada")
    x.add_argument("--vigilar", action="store_true", help="bucle continuo")
    x.add_argument("--intervalo", type=int, default=900); x.add_argument("--ciclos", type=int, default=0)
    x.set_defaults(f=cmd_auto)

    x = s.add_parser("proveedor-enviar", help="enviar un asset a una herramienta con API (OpusClip)")
    x.add_argument("--proveedor", required=True); x.add_argument("--asset", required=True)
    x.add_argument("--plataforma", default="tiktok"); x.add_argument("--tema", required=True)
    x.add_argument("--dolor", required=True); x.add_argument("--beneficio", required=True)
    x.set_defaults(f=cmd_proveedor_enviar)

    x = s.add_parser("proveedor-preparar", help="generar brief + carpeta de trabajo (SendShort, CapCut)")
    x.add_argument("--proveedor", required=True); x.add_argument("--asset", required=True)
    x.add_argument("--plataforma", default="tiktok"); x.add_argument("--tema", required=True)
    x.add_argument("--dolor", required=True); x.add_argument("--beneficio", required=True)
    x.add_argument("--objecion", default="")
    x.set_defaults(f=cmd_proveedor_preparar)

    x = s.add_parser("proveedor-estado", help="consultar trabajos en curso")
    x.add_argument("--todo", action="store_true")
    x.set_defaults(f=cmd_proveedor_estado)

    x = s.add_parser("render", help="re-ingestar el video terminado (pasa detector de marca de agua)")
    x.add_argument("--clip", required=True); x.add_argument("--archivo", required=True)
    x.add_argument("--proveedor", default="manual"); x.add_argument("--forzar", action="store_true")
    x.set_defaults(f=cmd_render)

    x = s.add_parser("avisar", help="enviar los pendientes por Telegram/webhook")
    x.add_argument("--url-panel", default="", help="URL publica del panel para el boton del mensaje")
    x.set_defaults(f=cmd_avisar)

    s.add_parser("pendientes", help="ver que espera tu autorizacion").set_defaults(f=cmd_pendientes)

    x = s.add_parser("autorizar", help="aprobar o rechazar un clip pendiente")
    x.add_argument("--token", required=True); x.add_argument("--rechazar", action="store_true")
    x.set_defaults(f=cmd_autorizar)

    x = s.add_parser("kit", help="kit de publicacion de un clip (titulos, descripcion, hashtags)")
    x.add_argument("clip"); x.add_argument("--plataforma", default="tiktok")
    x.add_argument("--json", action="store_true", help="salida en JSON")
    x.add_argument("--llm", action="store_true", help="pedir titulos extra al LLM configurado")
    x.add_argument("--guardar", default="", help="escribir el kit en un archivo")
    x.set_defaults(f=cmd_kit)

    x = s.add_parser("telegram-setup", help="verificar el bot y descubrir tu chat_id")
    x.add_argument("--chat-id", default=""); x.add_argument("--passphrase", default="")
    x.set_defaults(f=cmd_telegram_setup)

    s.add_parser("telegram-probar", help="mandar un mensaje de prueba").set_defaults(f=cmd_telegram_probar)

    s.add_parser("telegram-escuchar", help="recibir los toques de Aprobar/Rechazar").set_defaults(f=cmd_telegram_escuchar)

    x = s.add_parser("panel", help="panel web local")
    x.add_argument("--host", default="0.0.0.0"); x.add_argument("--puerto", type=int, default=8000)
    x.set_defaults(f=cmd_panel)

    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
