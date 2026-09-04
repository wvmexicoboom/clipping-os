"""
Clipping OS — sistema de operacion para clipping pagado (Whop Content Rewards y similares).

Arquitectura:
    db          capa de persistencia (SQLite, stdlib)
    compliance  compuerta obligatoria antes de producir o publicar
    discovery   ingesta y puntaje de campanas
    clip_spec   generacion del brief de edicion (hook + estructura + copy)
    publish     cola de publicacion + clientes API oficiales (TikTok / Instagram)
    earnings    calculo de pagos por 1,000 vistas verificadas y reportes
    app         panel web local (stdlib http.server)

Ningun modulo publica sin pasar por compliance.check_clip().
"""

__version__ = "1.0.0"
