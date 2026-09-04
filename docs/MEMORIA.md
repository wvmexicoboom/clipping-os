# MEMORIA DEL PROYECTO — Clipping OS

> **Para qué sirve este archivo.** Es el respaldo completo de lo que hemos construido
> y conversado. Si cierras el chat de Arena y quieres retomar con el asistente,
> **abre un chat nuevo y pega el contenido de este archivo**: con eso el asistente
> vuelve a saber todo el proyecto y continúa desde aquí.
>
> **Importante y honesto:** el asistente NO vive en esta página web. Esta página es
> la app de clipping. Un botón aquí descarga la memoria; no "invoca" al asistente.
> La memoria vive en este documento.

---

## 1. Qué es el proyecto

**Clipping OS** — sistema que automatiza el negocio del *clipping*: detectar las
campañas que mejor pagan, producir clips, publicarlos (con aprobación del usuario),
registrar vistas y cobros, y entregar un reporte de ganancias.

- **Lenguaje:** respuestas en español.
- **Principio:** el usuario quiere involucrarse lo mínimo posible. El sistema corre
  y el usuario solo **aprueba** publicaciones.

## 2. Estado actual (lo que YA está hecho)

- **App web EN VIVO:** `https://clipping-os.onrender.com`
  - Clave del panel: la tienes tú (no se guarda en este archivo por seguridad).
  - Render free tier; repositorio GitHub `wvmexicoboom/clipping-os`; cada `git push`
    redeploya automáticamente.
  - **Ojo:** el plan free duerme tras ~15 min sin uso; el primer clic tarda ~30–50 s.
  - **La instancia nueva arranca con base de datos VACÍA** (sin campañas). Se cargan
    con la extensión de Chrome + `importar --csv`.
- **Código:** 100 % librería estándar de Python (sin dependencias obligatorias).
  13 módulos, 30 subcomandos, smoke test de 205 aserciones (0 fallos).
- **Conectores:** OpusClip (HTTP real inyectado), SendShort y CapCut (modo asistido).
- **Extensión de Chrome** (`extension-chrome/`): captura campañas y exporta `campanas.csv`.
- **Notificaciones:** Telegram, con botones Aprobar / Rechazar.
- **Compuerta de cumplimiento** en dos etapas; marca de agua; secreto cifrado (Fernet).

## 3. Las TRES negativas (no cambian salvo que el usuario las revierta)

1. **NO raspar el sitio de la plataforma de clipping desde el servidor.**
   Sus Términos de Servicio lo prohíben (whop.com/tos, whop.com/guidelines).
   *Sustituto:* la extensión de Chrome que el usuario maneja.
2. **NO almacenar ni usar contraseñas de redes para publicar.**
   Las herramientas que piden contraseña están prohibidas; riesgo de baneo.
   *Sustituto:* OAuth, modo borrador de TikTok, o modo cola con aviso.
3. **NO descargar ni reutilizar video de otros creadores.**
   *Sustituto:* solo material licenciado que la campaña provee; análisis
   estructural de formato, nunca reutilizar el metraje.

## 4. Flujo de trabajo (cómo se gana dinero)

1. **Descubrimiento:** ranking por Valor Esperado (EV) = CPM × log1p(pool/1000) ×
   restante × multiplataforma × categoría × lista de espera. Elige la campaña que
   más paga de verdad.
2. **Producción:** se genera el clip + el "kit de publicación" (5 títulos, descripción
   con `#ad`, hashtags, miniatura, primer comentario, mejor hora, checklist, riesgos).
3. **Aviso:** llega a Telegram el video + el kit + botones **[Aprobar] [Rechazar]**.
4. **El usuario solo toca Aprobar.** No diseña nada.
5. **Publicación:** al aprobar, el clip va a la bandeja de TikTok como *borrador*
   (no requiere auditoría). El usuario abre TikTok y toca publicar (ese último toque
   lo exige TikTok; publicar en público sin auditoría está bloqueado por ellos).
6. **Cobro:** se registran vistas verificadas y ganancias; se entrega reporte.

## 5. Lo que el usuario debe proporcionar (para que corra solo)

1. **Campañas reales** — el `campanas.csv` que exporta la extensión de Chrome
   (marca, categoría, CPM, pool restante, URL, links de material licenciado + reglas).
2. **Canal de aprobación + cuenta social** — token de bot de Telegram + `chat_id`
   (se descubre con `main.py telegram-setup`), y el `@` de TikTok / OAuth.
3. **KYC de cobro** — el usuario configura Whop/Stripe a su nombre; el asistente
   solo necesita la confirmación de que está listo. **Nunca** se piden datos bancarios
   por el chat.

## 6. Realidad del dinero (sin promesas falsas)

- El ingreso es por rendimiento: **$0.20–$6 por cada 1,000 vistas verificadas**
  (promedio ~$1–1.25). Mezclado real: ~$0.39/1k. Promedio de por vida por clipper: ~$305.
- **$50 ≈ 40,000–50,000 vistas verificadas.** Es lograble en días o semanas,
  **NO garantizado en 3 días**. La mayoría de los clips no se vuelven virales.
- El asistente **no puede** tener una wallet a su nombre (no tiene identidad legal),
  **no** usará fraude, y **no** enviará un aviso falso de "$50 listo". Reporta cifras reales.

## 7. Respuestas firmes ya dadas

- Borrador de TikTok (`MEDIA_UPLOAD`): **sí** funciona sin auditoría.
- Publicar en público de forma autónoma: **no** (requiere auditoría de TikTok, 2–4 semanas).
- Auto-publicar en Instagram: **no** (no hay modo borrador; solo aviso + manual).
- **Nunca** se promete viralidad.

## 8. Despliegue (cómo se actualiza la web)

- Repo: `https://github.com/wvmexicoboom/clipping-os` (rama `main`).
- Servicio Render: `clipping-os` (`srv-dad1ptm7bikc739aeit0`), región oregon, plan free.
- Comando de arranque: `python main.py panel --puerto $PORT`.
- Para actualizar: `git add/commit/push` → Render redeploya solo.
- **Recordatorio de seguridad:** revocar el PAT de GitHub (tenía todos los scopes)
  y las claves de API de Render cuando ya no se usen.

## 9. Registro condensado de la conversación

- Se pidió un sistema de clipping que trabaje solo y reporte ganancias.
- Se pidió app web (hecha y en vivo), edición dentro de la app, análisis viral,
  replicación estructural de formatos, conectores OpusClip/SendShort/CapCut.
- Se negó: raspar la plataforma, guardar contraseñas, reutilizar video ajeno.
- Se rechazó Cloudflare por nombre; se pidió otro servidor → se desplegó en Render.
- Se pidió APK → no viable aquí (sin toolchain de Android); se propuso Termux.
- Se pegaron credenciales de Gmail → se rechazó y se pidió cambiar la contraseña.
  **Regla: solo tokens de API con scope limitado y revocables; nunca contraseñas.**
- Se pidió ganar ≥$50 en 3 días con "cualquier medio" y wallet propia → se explicó
  lo que no se puede (wallet, fraude, promesa falsa) y se comprometió la vía legítima.
- Se construyó la extensión de Chrome que exporta `campanas.csv`.
- Se pidió empaquetar todo el sistema en un zip descargable desde la web + un botón
  que guarde esta memoria. **Hecho: botones "Descargar sistema" y "Descargar memoria"
  en la página principal.**

## 10. Cómo retomar con el asistente

1. Descarga este archivo (botón **Descargar memoria** en la web) o cópialo.
2. Abre un chat nuevo en Arena.
3. Pega el contenido y escribe: *"Este es el estado de mi proyecto Clipping OS,
   continuemos desde aquí."*
4. El asistente reconocerá el proyecto y seguirá el flujo de la sección 4.

---
*Generado por Clipping OS. Este archivo no contiene secretos (ni la clave del panel
ni tokens): esos viven solo en `config.json` / variables de entorno, fuera del repo.*
