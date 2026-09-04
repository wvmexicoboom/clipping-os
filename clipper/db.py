"""Capa de persistencia. SQLite via stdlib, sin dependencias externas."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

RUTA_DB = os.environ.get("CLIPPER_DB", os.path.join(os.path.dirname(__file__), "..", "clipping.db"))

ESQUEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id                  TEXT PRIMARY KEY,
    plataforma          TEXT NOT NULL,
    url                 TEXT NOT NULL UNIQUE,
    marca               TEXT,
    titulo              TEXT,
    categoria           TEXT,
    cpm_usd             REAL,
    presupuesto_total   REAL,
    presupuesto_rest    REAL,
    plataformas_ok      TEXT DEFAULT '[]',
    min_views           INTEGER DEFAULT 0,
    cap_por_clip_usd    REAL,
    requiere_waitlist   INTEGER DEFAULT 0,
    reglas_texto        TEXT,
    fecha_fin           TEXT,
    estado              TEXT DEFAULT 'nueva',
    puntaje             REAL DEFAULT 0,
    visto_por_primera_vez TEXT,
    notas               TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id              TEXT PRIMARY KEY,
    campaign_id     TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL,
    ruta_o_url      TEXT NOT NULL,
    duracion_seg    REAL,
    licencia        TEXT NOT NULL DEFAULT 'desconocida',
    licencia_prueba TEXT,
    descargado      INTEGER DEFAULT 0,
    UNIQUE(campaign_id, ruta_o_url)
);

CREATE TABLE IF NOT EXISTS clips (
    id                  TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    titulo              TEXT,
    hook                TEXT,
    copy                TEXT,
    duracion_seg        REAL,
    asset_origen        TEXT,
    estado              TEXT DEFAULT 'idea',
    archivo_salida      TEXT,
    compliance_ok       INTEGER DEFAULT 0,
    compliance_motivos  TEXT DEFAULT '[]',
    creado_en           TEXT,
    enviado_en          TEXT
);

CREATE TABLE IF NOT EXISTS posts (
    id              TEXT PRIMARY KEY,
    clip_id         TEXT NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    campaign_id     TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    plataforma      TEXT NOT NULL,
    cuenta          TEXT,
    url_post        TEXT,
    estado          TEXT DEFAULT 'en_cola',
    publicado_en    TEXT,
    views           INTEGER DEFAULT 0,
    views_verif     INTEGER DEFAULT 0,
    likes           INTEGER DEFAULT 0,
    pagado_usd      REAL DEFAULT 0,
    pendiente_pago  INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS view_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    visto_en    TEXT NOT NULL,
    views       INTEGER NOT NULL,
    views_verif INTEGER,
    fuente      TEXT
);

CREATE TABLE IF NOT EXISTS payouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plataforma  TEXT NOT NULL,
    monto_usd   REAL NOT NULL,
    fecha       TEXT NOT NULL,
    metodo      TEXT,
    comision    REAL DEFAULT 0,
    notas       TEXT
);

CREATE TABLE IF NOT EXISTS provider_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    proveedor    TEXT NOT NULL,
    campaign_id  TEXT REFERENCES campaigns(id) ON DELETE SET NULL,
    asset_id     TEXT,
    referencia   TEXT,
    estado       TEXT DEFAULT 'enviado',
    n_clips      INTEGER,
    extra        TEXT,
    creado_en    TEXT
);

CREATE TABLE IF NOT EXISTS bot_state (
    clave   TEXT PRIMARY KEY,
    valor   TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    post_id     TEXT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    token       TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'pendiente',
    canal       TEXT,
    nota        TEXT,
    motivo      TEXT,
    creado_en   TEXT,
    resuelto_en TEXT
);

CREATE TABLE IF NOT EXISTS auto_state (
    fecha   TEXT NOT NULL,
    cuenta  TEXT NOT NULL,
    usados  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (fecha, cuenta)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    evento  TEXT NOT NULL,
    detalle TEXT
);

CREATE INDEX IF NOT EXISTS idx_posts_estado ON posts(estado);
CREATE INDEX IF NOT EXISTS idx_clips_estado ON clips(estado);
CREATE INDEX IF NOT EXISTS idx_campaigns_estado ON campaigns(estado);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conectar(ruta: str | None = None) -> sqlite3.Connection:
    path = ruta or RUTA_DB
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def sesion(ruta: str | None = None):
    conn = conectar(ruta)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def inicializar(ruta: str | None = None) -> str:
    with sesion(ruta) as c:
        c.executescript(ESQUEMA)
    return ruta or RUTA_DB


def log(conn: sqlite3.Connection, evento: str, detalle: Any = "") -> None:
    conn.execute(
        "INSERT INTO audit_log (ts, evento, detalle) VALUES (?,?,?)",
        (ahora(), evento, detalle if isinstance(detalle, str) else json.dumps(detalle, ensure_ascii=False)),
    )


def filas(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def uno(conn: sqlite3.Connection, sql: str, params: Iterable = ()) -> dict | None:
    cur = conn.execute(sql, tuple(params))
    r = cur.fetchone()
    return dict(r) if r else None
