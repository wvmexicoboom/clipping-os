# Clipping OS

Sistema de operación para **clipping pagado** (campañas tipo Whop Content Rewards):
descubre campañas, puntúa cuáles valen tu tiempo, genera briefs de edición, bloquea
lo que te costaría el pago o la cuenta, y te reporta únicamente el rendimiento en
dinero.

**Sin dependencias externas.** Núcleo en Python 3.10+ con librería estándar.

---

## Qué hace y qué no hace

| | |
|---|---|
| ✅ Descubre y puntúa campañas por **valor esperado**, no por CPM | ❌ No publica solo en TikTok/Instagram |
| ✅ Genera el brief exacto para tu herramienta de IA de video | ❌ No renderiza video (eso lo hace OpusClip/CapCut/SendShort) |
| ✅ Bloquea material sin licencia y copias de otros clippers | ❌ No compra ni simula vistas |
| ✅ Calcula el pago real: verificadas, umbral, tope, comisión, pool | ❌ No inicia sesión en tus redes |
| ✅ Reporta cobrado vs. estimado | ❌ No acepta categorías de riesgo legal |

El clic final de publicación es tuyo. No es una limitación técnica: ver
[`docs/LEGAL_Y_REALIDAD.md`](docs/LEGAL_Y_REALIDAD.md) §2b.

---

## Instalación

```bash
cd clipping-os
python main.py init                   # ya funciona sin config
cp config.example.json config.json    # opcional: cuentas, APIs, LLM
```

Las **reglas duras están activas por defecto**, sin `config.json`: categorías
bloqueadas (apuestas, casino, adulto, salud), CPM mínimo, pool máximo consumido,
divulgación obligatoria y prohibición de material sin licencia. El `config.json`
solo puede **agregar** restricciones, no quitarlas — `compliance.reglas_efectivas()`
ignora cualquier intento de apagarlas.


## Flujo completo

```bash
# 1. Cargar campañas (CSV exportado desde la extensión de Chrome o a mano)
python main.py importar --csv ejemplo_campanas.csv

# 2. Ver cuáles valen la pena, con veredicto de la mejor
python main.py ranking --limite 10 --verificar

# 3. Aprobar la elegida (la compuerta puede descartarla)
python main.py aprobar --id c_xxxxxxxxxxxx

# 4. Registrar el material que la CAMPAÑA te entregó (esto es lo que te autoriza)
python main.py asset --campana c_xxxx --tipo vod \
  --ruta assets/c_xxxx/vod_01.mp4 --licencia campana \
  --prueba "El brief autoriza a recortar y republicar este VOD"

# 5. Generar el brief de edición
python main.py brief --campana c_xxxx --plataforma tiktok \
  --tema "productividad para freelancers" \
  --dolor "pierdo horas cambiando de app" \
  --beneficio "todo tu trabajo en una sola pantalla"
# --objecion "ya probé otras apps"  activa además el gancho de tipo objeción
# → produce salida/brief_cl_xxxx.md  (se lo das a tu app de IA de video)

# 6. Pasar el clip por la compuerta y dejarlo en cola
python main.py verificar --campana c_xxxx --clip cl_xxxx
python main.py encolar --clip cl_xxxx --plataforma tiktok --cuenta @tu_usuario
python main.py cola                    # → salida/cola_de_hoy.json con checklist

# 7. Después de publicar tú, registra la URL y envíala a la campaña
python main.py publicado --post-id p_cl_xxxx_tiktok --url https://tiktok.com/@tu/video/123

# 8. Actualizar vistas y cobrar
python main.py vistas  --post-id p_cl_xxxx_tiktok --views 48200 --verificadas 45100
python main.py pago    --plataforma whop --monto 40.14 --metodo stripe
python main.py reporte --dias 7
```

## Notificacion y autorizacion: el sistema produce, tu decides

Este es el flujo completo que corre solo hasta el ultimo paso:

```
descubre campana → escribe el brief → produce el clip (OpusClip / CapCut / SendShort)
  → revisa marca de agua → lo deja pendiente
  → te avisa por TELEGRAM con el video adjunto + copy sugerido + botones
  → tu tocas "Aprobar"
  → queda en la cola para publicarse
```

**Nada se publica sin que toques Aprobar.** No es una limitacion del codigo: es el
diseno. Cada pendiente lleva un token aleatorio de 32 bytes; sin el token correcto
la decision no se registra (devuelve 403) y un enlace ya usado no se puede reutilizar.

| Comando / ruta | Que hace |
|---|---|
| `main.py telegram-setup` | Valida el bot y descubre tu chat_id solo |
| `main.py telegram-probar` | Mensaje de prueba |
| `main.py telegram-escuchar` | Recibe los toques de Aprobar / Rechazar |
| `main.py kit <CLIP>` | Titulos, descripcion, hashtags y checklist del clip |
| `main.py pendientes` | Lista lo que espera tu decision |
| `main.py avisar --url-panel https://tu-panel` | Manda los pendientes por Telegram |
| `main.py autorizar --token <TOKEN>` | Aprueba |
| `main.py autorizar --token <TOKEN> --rechazar` | Descarta |
| `/autorizar` en el panel | Pagina con el video, el copy y botones Aprobar / Rechazar |
| `POST /api/autorizar` | `{"token": "...", "aprobar": true}` |

### Conectar Telegram (3 minutos)

```bash
# 1. En Telegram abre @BotFather → /newbot → copia el token
python3 main.py secretos-set --proveedor telegram --campo bot_token

# 2. Este comando valida el token y descubre tu chat_id SOLO
#    (te pide que le mandes /start al bot si aun no lo encuentra)
python3 main.py telegram-setup

# 3. Mensaje de prueba para confirmar que llega
python3 main.py telegram-probar

# 4. Dejar corriendo el receptor de botones
python3 main.py telegram-escuchar
```

**Importante:** mientras `telegram-escuchar` no corra, los botones del mensaje **no
hacen nada**. Telegram no empuja los toques: hay que ir a buscarlos con `getUpdates`.
Si prefieres no dejar ese proceso vivo, usa `/autorizar` en el panel: decide lo mismo.

El offset de `getUpdates` se guarda en la base (`bot_state`), asi que si el proceso se
reinicia no vuelve a aplicar una decision que ya tomaste.



### Por que TikTok sube a borradores y no publica directo

TikTok tiene dos modos y la diferencia es decisiva:

| | `DIRECT_POST` | `MEDIA_UPLOAD` (el que usamos) |
|---|---|---|
| Scope | `video.publish` | `video.upload` |
| Auditoria de TikTok | **Obligatoria** (2–4 semanas, varias rondas) | **No la requiere** |
| Sin auditoria pasa a | `SELF_ONLY` (privado, 0 vistas, 0 pago) | Funciona igual |
| Quien decide publicar | La API | **Tu, desde la app** |

O sea: la **subida es automatica** y la **decision sigue siendo tuya**, sin esperar
una auditoria y sin entregar contrasenas. En la bandeja de TikTok ademas puedes
agregar el audio en tendencia, y varios integradores documentan que los clips
terminados ahi tienden a tener mas alcance que los publicados directo por API.

**Restriccion real de TikTok que hay que saber:** en `MEDIA_UPLOAD` la API no permite
fijar titulo, caption ni privacidad. El video llega a tu bandeja y el copy se pone
dentro de la app. Por eso el copy va en el mensaje de Telegram: no es un descuido.

### Instagram no tiene modo borrador

La API de publicacion de Instagram **no soporta borradores ni programacion**: es
crear contenedor → publicar, inmediato. Ademas exige cuenta Business ligada a una
Page y revision de app de Meta. Para Instagram el flujo queda en: el sistema produce
el clip, te lo manda por Telegram con el copy, y tu lo subes desde la app (30 s).
No invente un endpoint que no existe.

## Kit de publicacion: todo lo que va junto al clip

Un clip bien editado con un titulo flojo no se distribuye. Cada borrador trae, junto
al video, el paquete completo listo para copiar y pegar:

**Titulos propuestos** (5 variantes, cada una con su justificacion) · **descripcion**
con conteo de caracteres contra el limite de la plataforma · **hashtags por nivel**
(nicho / medio / amplio, respetando el maximo recomendado) · **instruccion de
miniatura** · **primer comentario** · **ventanas de publicacion** · **checklist** ·
**riesgos detectados** en el texto.

```bash
python3 main.py kit cl_a1b2c3d4e5f6                  # texto plano
python3 main.py kit cl_a1b2c3d4e5f6 --json           # para integrar
python3 main.py kit cl_a1b2c3d4e5f6 --llm            # pide titulos extra al LLM
python3 main.py kit cl_a1b2c3d4e5f6 --guardar kit.txt
```

En el panel, el kit aparece **en la misma tarjeta que el video** en `/autorizar`, y en
Telegram va completo dentro del mensaje (porque en modo borrador de TikTok el caption
se pone dentro de la app).

### Nada de esto promete viralidad

Cada elemento lleva un campo `evidencia` con dos valores posibles:

| Etiqueta | Que significa |
|---|---|
| `restriccion de plataforma` | Regla dura: limite de caracteres, truncamiento en Shorts, conteo de hashtags. Verificable. |
| `heuristica` | Patron que suele funcionar. **No garantizado**, depende de tu audiencia. |

Se separan a proposito: si una heuristica se presenta con voz de hecho, optimizas para
algo que no existe. Y el kit lo dice explicitamente:

> Ningun kit hace viral un clip: la retencion del video manda, y eso se decide en la edicion.

### Dos guardas que evitan publicar basura

**Insumos insuficientes.** Si el tema, el dolor o el beneficio del brief estan vacios
o son demasiado largos para un titulo, el kit lo marca en vez de inventar:

```
⚠️ INSUMOS INSUFICIENTES: dolor, beneficio (demasiado largo para un titulo)
   — completa esos campos del brief de la campaña antes de usar estos títulos.
```

Sin esta guarda, un beneficio de 60 caracteres producia titulos como
`"t (sin te cuesta)"`: texto inservible que igual se iria al caption.

**Frases fragiles.** Detecta y avisa sobre palabras que reducen alcance o son claims
sin fuente: `gratis`, `garantizado`, `100%`, `hazte rico`, `link en bio`, `#fyp`,
`milagro`, `seguidores`, `suscribete`. Cada una con la razon.

Los cortes de texto son **por palabra completa**, nunca a mitad: recortar a 18
caracteres producia `#EpsilonSaaSnorequi`. Los hashtags de marca conservan CamelCase
(`#EpsilonSaaS`), los genericos van en minusculas.

El kit es **deterministico**: mismos insumos, mismo kit. Sin LLM obligatorio — si
configuras uno, agrega titulos, pero el kit base funciona igual y nunca deja que el
LLM invente cifras.

## Credenciales: se guardan una vez

```bash
python main.py secretos                                  # qué hay y dónde se consigue cada key
python main.py secretos-set --proveedor opusclip         # la pegas sin que se vea en pantalla
python main.py secretos-set --proveedor tiktok --campo client_secret
python main.py secretos-del --proveedor opusclip
```

Tres niveles, en orden de preferencia:

| Nivel | Dónde | Seguridad |
|---|---|---|
| 1 | `CLIPPER_SECRET_<PROVEEDOR>_<CAMPO>` (env) | Nada toca el disco |
| 2 | `secrets.enc` | **Fernet + PBKDF2-SHA256, 390k iteraciones**, con tu passphrase |
| 3 | `secrets.local.json` | `chmod 600` y en `.gitignore`. **No es cifrado**, solo permisos |

```bash
pip install cryptography        # habilita el nivel 2
python main.py secretos-set --proveedor opusclip --passphrase "tu frase"
```

Verificado: el valor en claro **no aparece** en `secrets.enc`, la passphrase equivocada
no lo devuelve, y ambos archivos quedan en `0600`. El panel web y la CLI **nunca**
imprimen el valor completo — solo `sk-w••••••••••7766`.

También hay página web: `http://localhost:8000/credenciales`. Verificado por HTTP: el
`POST` guarda y el `GET` **no** devuelve el valor en ninguna respuesta.

### Por qué no guardo usuarios y contraseñas

Me lo pediste y es lo único de tu lista que no hice. Tres razones, cualquiera suficiente:

1. **Automatizar el login viola los términos** de OpusClip, SendShort, CapCut, TikTok e
   Instagram, y es el patrón que usan para suspender cuentas.
2. **Una contraseña da acceso a todo**: correo de recuperación, facturación, tus otras
   cuentas. Una API key da acceso a una función y **se revoca en un clic**.
3. **No hace falta.** Las tres herramientas generan la API key entrándote una vez a mano
   a su panel. `python main.py secretos` te dice exactamente en qué pantalla está cada una.

O sea: entras una vez, copias la key, la pegas, y ya no vuelves a entrar. El resultado
práctico que querías —no repetir el login— queda cubierto sin el riesgo.

## Ciclo autónomo

```bash
python main.py auto --dry-run                 # genera briefs sin enviar nada
python main.py auto --max 3                   # un ciclo: cosecha + produce
python main.py auto --vigilar --intervalo 900 # bucle continuo (15 min)
```

| Sí corre solo | Nunca corre solo |
|---|---|
| Elegir la campaña de mejor valor esperado que pase la compuerta | **Publicar** — el clic final es tuyo |
| Generar los briefs por plataforma | Saltarse la compuerta |
| Enviar a la herramienta **con API** y consultar su estado | Usar una herramienta sin API (prepara carpeta y avisa) |
| Cosechar el render, pasar la compuerta y dejarlo en la cola | Superar el tope diario |

**Tope diario persistido** en la base por fecha UTC (`max_por_cuenta_dia`, con tope duro
de 25 que no se puede subir por config): reiniciar el proceso no reinicia la cuota.
El detector de marca de agua puede dejar un clip en `revision_agua` y **frena el ciclo**,
que es justo lo que debe poder hacer.

## Herramientas de IA de video conectables

```bash
python main.py proveedores          # estado real de cada una
```

| Herramienta | API | Modo | Cómo se conecta |
|---|---|---|---|
| **OpusClip** | ✅ Sí | `api` | `POST /upload-links` → subida resumible a GCS → `POST /clip-projects` → `GET /exportable-clips`. Autenticación Bearer. |
| **SendShort** | ❌ No | `asistido` | Genera brief + carpeta de trabajo; tú exportas y el sistema re-ingesta el archivo. |
| **CapCut** | ❌ No | `asistido` | Ídem, más un `beats.json` con la estructura para un generador de drafts. |

Estado verificado contra documentación oficial (2026-09): el **help center de OpusClip**
dice que la API está en beta cerrada para planes anuales de alto volumen, mientras su
**sitio comercial** dice que empieza en Pro — las fuentes se contradicen, así que el
conector degrada con un mensaje claro si tu plan no tiene acceso. **SendShort** no
documenta API en su help center y las comparativas independientes lo confirman.
**CapCut** no tiene API de renderizado: su "Open Platform" es para plugins *dentro* del
editor y su "AI API" se limita a texto-a-video y plantillas.

```bash
# Con API (OpusClip)
python main.py proveedor-enviar --proveedor opusclip --asset a_xxxx \
  --tema "..." --dolor "..." --beneficio "..."
python main.py proveedor-estado

# Sin API (SendShort, CapCut): brief + carpeta de trabajo
python main.py proveedor-preparar --proveedor capcut --asset a_xxxx \
  --tema "..." --dolor "..." --beneficio "..." --objecion "..."

# Re-ingesta del video terminado (cualquier proveedor)
python main.py render --clip cl_xxxx --archivo salida/export.mp4 --proveedor capcut
```

**Regla invariante:** ningún video vuelve a la cola sin pasar otra vez por la compuerta,
incluido el **detector de marca de agua**. Muestrea 6 frames del clip y busca, en las
cuatro esquinas, pixeles con varianza temporal casi nula + brillo alto + saturación baja
— o sea, un logo estático sobre video que sí se mueve. Verificado: 0.0% en video real
sin marca, 22.6% localizado en `inf_der` cuando la marca está ahí.

Esto importa porque **el plan gratuito de OpusClip exporta con marca de agua** y TikTok
rechaza videos con marcas de agua de otras apps.

Es una heurística, no una garantía: un fondo fijo y claro puede dar falso positivo y una
marca animada puede pasar. Por eso el clip queda en estado `revision_agua` y tú decides
con `--forzar`.

## Panel web

```bash
python main.py panel --puerto 8000
```
Panel local en `http://localhost:8000` — campanas por puntaje, compuerta de
cumplimiento y detalle por post.

## Extensión de Chrome

Carpeta `extension-chrome/`. Chrome → `chrome://extensions` → *Modo desarrollador*
→ *Cargar descomprimida*.

Captura los datos visibles de la campaña, los guarda localmente y exporta un CSV
compatible con `main.py importar`. **No** hace clics por ti ni rellena compositores
de redes sociales.

## Con Manus

Pega [`docs/manus/INSTRUCCION.md`](docs/manus/INSTRUCCION.md) como primer mensaje
de una tarea nueva. Ese archivo define reglas duras, el contrato de salida en CSV,
lo que está prohibido y el criterio de terminado.

Manus cubre descubrimiento, puntaje, briefs y reporte. La publicación no.

---

## Estructura

```
clipping-os/
├── main.py                     CLI
├── config.example.json         configuración (copiar a config.json)
├── ejemplo_campanas.csv        datos de prueba
├── clipper/
│   ├── db.py                   SQLite (stdlib)
│   ├── compliance.py           la compuerta — nada se produce/publica sin pasar por aquí
│   ├── discovery.py            ingesta + puntaje por valor esperado
│   ├── clip_spec.py            brief de edición (gancho, beats, copy, notas)
│   ├── publish.py              cola + APIs oficiales (TikTok/Instagram)
│   ├── earnings.py             liquidación real y reportes
│   ├── app.py                  panel web local
│   ├── providers/              OpusClip (API), SendShort, CapCut + detector de marca de agua
│   ├── secrets.py              API keys en 3 niveles (env / Fernet / 0600)
│   ├── auto.py                 ciclo autónomo con tope diario persistido
│   ├── notify.py               Telegram + webhook, pendientes y autorizacion por token
│   └── viral.py                kit de publicacion: titulos, descripcion, hashtags, checklist
├── extension-chrome/           MV3: captura y exportación
└── docs/
    ├── ARCHITECTURA.md
    ├── LEGAL_Y_REALIDAD.md
    ├── DESPLIEGUE.md               servidor gratuito: requisitos, Docker, systemd
    └── manus/INSTRUCCION.md
```

## Pruebas

```bash
python test_smoke.py
```

Recorre el flujo completo sobre una base temporal: importación, puntaje, compuertas,
activos, brief, encolado, liquidación, reporte y panel web. Cubre explícitamente los
casos que **deben fallar**:

| Caso | Resultado esperado |
|---|---|
| Clip con material sin licencia | `bloqueado` + excepción al encolar |
| Clip clonado de otro usuario | `bloqueado` |
| Copy sin `#ad` | `bloqueado` |
| Claim de riesgo ("rentabilidad asegurada") | `bloqueado` |
| Video de 2 s | `bloqueado` (TikTok exige ≥3 s) |
| Campaña de casino con CPM $6 | descartada y hundida en el ranking |
| Pool 89 % consumido | descartada |
| CPM $0.20 | descartada |
| Clip inexistente en la base | excepción, no silencio |
| `publicar_tiktok` sin auditoría aprobada | excepción, no publicación privada |
| Vistas dentro de la ventana de 5 días | $0 con el motivo |
| Vistas bajo el umbral mínimo | $0 con el motivo |
| 5 M de vistas en un clip | limitado al tope por clip |
| Upsert parcial de una campaña | no vacía el CPM ni el pool |
| `config.json` con `bloquear_categorias: []` | el filtro **sigue activo** |
| OpusClip sin `api_key` | excepción antes de tocar la red |
| OpusClip responde 401 | excepción legible, no traceback |
| Flujo OpusClip con HTTP inyectado | los 4 pasos de la doc oficial, en orden |
| Video real **sin** marca de agua | 0.0%, pasa |
| Video real **con** marca de agua | 22.6% en la esquina correcta, bloquea |
| Render con marca de agua | estado `revision_agua`, no entra a la cola |
| Proveedor inexistente | excepción, no KeyError |
| Guardar con passphrase | el valor en claro **no** está en `secrets.enc`, permisos `0600` |
| Passphrase equivocada | no devuelve el valor |
| Variable de entorno | tiene prioridad sobre los archivos |
| `dry-run` | no envía, no registra trabajos, no consume cuota |
| Tope diario alcanzado | se detiene, y la cuota sobrevive al reinicio |
| CapCut en el ciclo automático | prepara carpeta, no finge un envío |
| OpusClip sin credencial en el ciclo | omite e indica el comando exacto |
| `GET /api/secretos` y `/credenciales` | el valor guardado **no aparece** en la respuesta |
