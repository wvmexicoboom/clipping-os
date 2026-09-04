"""
Credenciales y secretos.

IMPORTANTE — que se guarda y que no:

  SI se guardan   API keys y tokens. Es la forma correcta: las herramientas las
                  emiten justo para esto, se pueden revocar una por una desde su
                  panel, y no dan acceso a tu correo ni a tu cuenta completa.

  NO se guardan   usuarios y contraseñas de OpusClip/SendShort/CapCut/TikTok.
                  Tres razones, cualquiera suficiente:
                    1. Automatizar el login viola los terminos de esas plataformas
                       y es el patron que detectan para suspender cuentas.
                    2. Una contraseña da acceso a TODO (correo de recuperacion,
                       facturacion, otras cuentas). Una API key da acceso a una
                       funcion y se revoca en un clic.
                    3. Ninguna de las tres herramientas necesita tu contraseña:
                       las API keys se generan logueandose UNA vez a mano en su
                       panel. Este modulo guarda donde queda cada una para que no
                       lo busques cada vez.

Niveles de almacenamiento, en orden de preferencia:

  1. Variable de entorno        CLIPPER_SECRET_<PROVEEDOR>_<CAMPO>
                                Lo mejor: nada toca el disco.
  2. Archivo cifrado (Fernet)   secrets.enc, clave derivada por PBKDF2-HMAC-SHA256
                                de tu passphrase (390k iteraciones). Requiere
                                `pip install cryptography`.
  3. Archivo plano chmod 600    secrets.local.json, en .gitignore.
                                Funciona siempre, pero NO es cifrado: es solo
                                permisos de archivo. El modulo lo advierte.

Ningun secreto se imprime completo nunca: `enmascarar()` es lo unico que sale.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _rutas() -> tuple[Path, Path]:
    """Rutas de los archivos de secretos. Configurables para poder probar sin
    tocar los archivos reales del proyecto."""
    base = Path(os.environ.get("CLIPPER_SECRETS_DIR", str(ROOT)))
    return base / "secrets.enc", base / "secrets.local.json"


ITERACIONES = 390_000

# Directorio de donde se saca cada credencial. No es secreto: es para que no
# tengas que buscarlo cada vez.
DIRECCIONES = {
    "opusclip": {
        "login": "https://www.opus.pro/login",
        "api_key_en": "Panel de Opus → Settings → API (esquina inferior izquierda). Se llama Organization API Key.",
        "docs": "https://help.opus.pro/api-reference",
        "requiere": "Plan de pago. El help center dice beta cerrada para planes anuales de alto volumen; el sitio comercial dice que empieza en Pro.",
    },
    "sendshort": {
        "login": "https://sendshort.ai/login",
        "api_key_en": "No existe. SendShort no expone API publica, asi que no hay key que generar.",
        "docs": "https://help.sendshort.ai",
        "requiere": "Nada: se usa en modo asistido (carpeta de trabajo + re-ingesta).",
    },
    "capcut": {
        "login": "https://www.capcut.com/login",
        "api_key_en": "No existe API de renderizado. Su 'Open Platform' es para plugins dentro del editor.",
        "docs": "https://www.capcut.com",
        "requiere": "Nada: modo asistido.",
    },
    "tiktok": {
        "login": "https://developers.tiktok.com",
        "api_key_en": "developers.tiktok.com → crea una app → agrega el producto Content Posting API → solicita los scopes video.upload / video.publish.",
        "docs": "https://developers.tiktok.com/doc/content-posting-api-get-started",
        "requiere": "Auditoria aprobada para publicar en publico. Sin auditoria, todo post sale SELF_ONLY (privado) y no genera vistas ni pago.",
    },
    "instagram": {
        "login": "https://developers.facebook.com",
        "api_key_en": "developers.facebook.com → app → Instagram Graph API. Necesitas cuenta Business/Creator y pagina de Facebook enlazada.",
        "docs": "https://developers.facebook.com/docs/instagram-api",
        "requiere": "App de Meta verificada.",
    },
    "llm": {
        "login": "https://platform.openai.com/api-keys",
        "api_key_en": "platform.openai.com → API keys → Create new secret key.",
        "docs": "https://platform.openai.com/docs",
        "requiere": "Opcional. Sin key, los briefs se generan con la biblioteca local.",
    },
    "telegram": {
        "login": "https://t.me/BotFather",
        "api_key_en": "En Telegram abre @BotFather → /newbot → copia el token (formato 123456:ABC-DEF...). "
                      "El chat_id no lo buscas a mano: `main.py telegram-setup` lo descubre solo.",
        "docs": "https://core.telegram.org/bots/api",
        "requiere": "Gratis y sin revision de nadie. Es el canal por donde te llega cada clip "
                    "para aprobar o rechazar. Limite real: ~1 mensaje por segundo por chat "
                    "y 50 MB por archivo adjunto.",
    },
}


def _var_entorno(proveedor: str, campo: str) -> str:
    return f"CLIPPER_SECRET_{proveedor.upper()}_{campo.upper()}"


def enmascarar(valor: str | None, visibles: int = 4) -> str:
    if not valor:
        return "(vacio)"
    if len(valor) <= visibles * 2:
        return "•" * len(valor)
    return f"{valor[:visibles]}{'•' * 10}{valor[-visibles:]}"


def _leer_plano() -> dict:
    ruta = _rutas()[1]
    if not ruta.exists():
        return {}
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _escribir_plano(datos: dict) -> None:
    ruta = _rutas()[1]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(ruta, stat.S_IRUSR | stat.S_IWUSR)   # 0600
    except OSError:
        pass


def _derivar_clave(passphrase: str, sal: bytes) -> bytes:
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), sal, ITERACIONES)
    return base64.urlsafe_b64encode(dk)


def _leer_cifrado(passphrase: str) -> dict:
    ruta = _rutas()[0]
    if not ruta.exists():
        return {}
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError:
        return {}
    blob = json.loads(ruta.read_text(encoding="utf-8"))
    try:
        f = Fernet(_derivar_clave(passphrase, base64.b64decode(blob["sal"])))
        return json.loads(f.decrypt(blob["datos"].encode()))
    except (InvalidToken, KeyError, ValueError):
        return {}


def _escribir_cifrado(datos: dict, passphrase: str) -> bool:
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return False
    sal = os.urandom(16)
    f = Fernet(_derivar_clave(passphrase, sal))
    blob = {"sal": base64.b64encode(sal).decode(),
            "kdf": f"pbkdf2_sha256_{ITERACIONES}",
            "datos": f.encrypt(json.dumps(datos).encode()).decode()}
    ruta = _rutas()[0]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(blob, indent=2), encoding="utf-8")
    try:
        os.chmod(ruta, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    return True


def guardar(proveedor: str, campo: str, valor: str, passphrase: str | None = None) -> str:
    """
    Guarda un secreto UNA vez y devuelve el nivel donde quedo.
    Con `passphrase` se cifra; sin el, va al archivo plano con 0600.
    """
    proveedor = proveedor.strip().lower()
    campo = campo.strip().lower()
    if not valor:
        raise ValueError("el valor no puede estar vacio")

    if passphrase:
        datos = _leer_cifrado(passphrase)
        datos[f"{proveedor}.{campo}"] = valor
        if _escribir_cifrado(datos, passphrase):
            return "cifrado (secrets.enc, Fernet + PBKDF2)"
        raise RuntimeError(
            "No se pudo cifrar: falta la libreria 'cryptography'. "
            "Instalala con `pip install cryptography`, o guarda sin passphrase "
            "(archivo plano chmod 600), o usa una variable de entorno."
        )

    datos = _leer_plano()
    datos[f"{proveedor}.{campo}"] = valor
    _escribir_plano(datos)
    return "archivo plano chmod 600 (secrets.local.json) — NO es cifrado"


def obtener(proveedor: str, campo: str, passphrase: str | None = None) -> str | None:
    """Busca en los tres niveles, en orden de preferencia."""
    v = os.environ.get(_var_entorno(proveedor, campo))
    if v:
        return v
    if passphrase:
        v = _leer_cifrado(passphrase).get(f"{proveedor.strip().lower()}.{campo.strip().lower()}")
        if v:
            return v
    return _leer_plano().get(f"{proveedor.strip().lower()}.{campo.strip().lower()}")


def borrar(proveedor: str, campo: str, passphrase: str | None = None) -> bool:
    clave = f"{proveedor.strip().lower()}.{campo.strip().lower()}"
    cambiado = False
    plano = _leer_plano()
    if clave in plano:
        del plano[clave]
        _escribir_plano(plano)
        cambiado = True
    if passphrase and _rutas()[0].exists():
        cif = _leer_cifrado(passphrase)
        if clave in cif:
            del cif[clave]
            _escribir_cifrado(cif, passphrase)
            cambiado = True
    return cambiado


def nivel_actual(proveedor: str, campo: str, passphrase: str | None = None) -> str:
    if os.environ.get(_var_entorno(proveedor, campo)):
        return "variable de entorno"
    if passphrase and _leer_cifrado(passphrase).get(f"{proveedor}.{campo}"):
        return "cifrado (secrets.enc)"
    if _leer_plano().get(f"{proveedor}.{campo}"):
        return "archivo plano 0600"
    return "sin guardar"


def inventario(passphrase: str | None = None) -> list[dict]:
    """Estado de cada credencial conocida. Nunca devuelve el valor completo."""
    campos_por_proveedor = {
        "opusclip": ["api_key"], "tiktok": ["client_key", "client_secret", "access_token"],
        "instagram": ["access_token", "ig_user_id"], "llm": ["api_key"],
        "telegram": ["bot_token", "chat_id"],
        "sendshort": [], "capcut": [],
    }
    out = []
    for prov, campos in campos_por_proveedor.items():
        info = DIRECCIONES.get(prov, {})
        regs = []
        for c in campos:
            val = obtener(prov, c, passphrase)
            regs.append({"campo": c, "guardado": bool(val), "muestra": enmascarar(val),
                         "nivel": nivel_actual(prov, c, passphrase)})
        out.append({"proveedor": prov, "credenciales": regs, **info})
    return out
