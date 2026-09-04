# Legalidad, riesgos y números reales

Este documento existe para que ninguna decisión se tome sobre un supuesto.

---

## 1. Lo que sí es legal y sí paga

El modelo de *clipping* de campañas (Whop Content Rewards y equivalentes) es
legítimo y real. Funciona porque:

- La marca **financia un pool** y fija una tasa por 1,000 vistas verificadas.
  Rango observado en 2026: **$0.20–$6**, con media alrededor de **$1–$1.25**.
- La marca **entrega el material de origen y una licencia de uso**. Ahí está la
  diferencia legal entre clipping y re-subida: no estás usando contenido ajeno sin
  permiso, estás distribuyendo contenido que su dueño te pidió distribuir.
- No se requieren seguidores mínimos ni horas de reproducción.
- El pago sale del pool de la campaña, no del reparto publicitario de la red social.

**México está en la lista de países soportados para retiros de Whop** (vía Whop
Payments / Stripe, con KYC: datos, banco e identificación). Verifícalo con tu
propia cuenta antes de invertir tiempo: las opciones de retiro han cambiado por
país y por fecha.

---

## 2. Lo que NO es legal ni sostenible

### a) Copiar los videos ganadores de otros clippers

Es el punto donde la propuesta original se cae. Consecuencias concretas, no
teóricas:

- **Derechos de autor.** El material de otro clipper o de un creador no es tuyo.
  La licencia de la campaña cubre *su* material, no el de terceros.
- **Duplicados filtrados.** Las campañas detectan re-subidas y contenido
  duplicado; esas vistas **no se pagan**.
- **Strike y suspensión.** TikTok e Instagram suspenden cuentas por contenido
  re-subido. Una suspensión **forfeita el saldo pendiente de pago** de la campaña.
- El resultado esperado de automatizar esto a escala es: mucho trabajo, cero cobro,
  cuentas perdidas.

**Lo que sí funciona:** tomar la **estructura** del video ganador (duración del
gancho, tipo de apertura, ritmo de cortes, dónde va el CTA) y aplicarla sobre el
material licenciado de la campaña. Eso es lo que hace `clip_spec.py`.

### b) Publicación 100% autónoma en TikTok / Instagram

- **TikTok Content Posting API:** publicar en **público** exige que tu app pase la
  **auditoría de TikTok** (revisión manual, semanas, con requisitos estrictos de UX:
  mostrar el creador, selector de privacidad sin valor por defecto, controles de
  duet/stitch/comentarios, divulgación de contenido comercial). **Mientras no la
  pases, todo lo que publique tu app sale en modo `SELF_ONLY`** — privado, invisible,
  cero vistas, cero pago. El modo `MEDIA_UPLOAD` manda el video a la bandeja del
  creador y requiere confirmación manual.
- **Instagram Graph API:** requiere cuenta Business/Creator, app de Meta verificada
  y página de Facebook enlazada.
- **Automatizar el navegador** viola los términos de ambas plataformas y es
  exactamente el patrón que detecta el filtro anti-bot de la campaña.

Por eso `publish.py` implementa tres modos y el modo por defecto es **cola**:
el sistema deja video, copy, hashtags y hora listos, y **tú haces el clic final**.
Es el único modo sin riesgo de suspensión.

### c) Divulgación

Es contenido pagado por una marca. Debe declararse (`#ad` / `#sponsored`, y la
etiqueta de colaboración pagada dentro de la propia app). La compuerta de
cumplimiento lo bloquea si falta. En México aplica el criterio de publicidad
identificable de PROFECO/CONDUSEF para contenido comercial en redes.

### d) Estafas alrededor del nicho

Regla simple: **un trabajo real de clipping nunca te cobra por empezar.** Si te
piden pagar por entrar a una "comunidad de clippers" o por un "sistema", es
estafa. Tampoco es creíble quien garantiza ingresos del primer mes.

---

## 3. Los números reales (para calibrar expectativas)

Filtros que reducen "vistas × CPM" a lo que de verdad se cobra:

| Filtro | Efecto |
|---|---|
| Vistas **verificadas**, no totales | Se descuentan bots y vistas sospechosas |
| Umbral mínimo por clip | Debajo de ~1,000–2,000 vistas, el clip paga **$0** |
| Tope por clip | Comúnmente **$100–$500**: un video viral no paga infinito |
| Comisión de plataforma | ~**9%** en Whop |
| Pool agotado | Cuando se seca, las vistas nuevas **no pagan nada** |
| Ventana de verificación | ~**5 días** antes de que sea pagable |

Dato duro de referencia pública de Whop: **$2.58 M pagados entre 8,466 clippers**,
es decir ≈ **$305 de ganancia acumulada promedio por clipper**, y un blended de
≈ **$0.39 por 1,000 vistas** — muy por debajo de las tasas anunciadas, justo por
los filtros de arriba. Los rangos que reportan clippers activos: ~$100–$500/mes
empezando, $500–$2,000/mes con volumen consistente, y una minoría muy por encima.

**Conclusión:** es ingreso por desempeño que escala con volumen de clips buenos y
velocidad de entrada a campañas frescas. No es ingreso pasivo y no es autónomo.
Quien promete $15k/mes suele estar vendiendo un curso.

**Sobre el "doble cobro":** el programa Creator Rewards de TikTok **excluye
contenido patrocinado o pagado**, así que un clip de campaña no genera ese segundo
ingreso en TikTok. Trátalo como bonus donde la plataforma lo permita, no como plan.

---

## 4. Sobre Manus como ejecutor

- Cada sesión estándar de Manus arranca en una VM nueva: **no hay memoria entre
  sesiones** ni estado persistente.
- **Cloud Computer** (el plan persistente) sí da una máquina Ubuntu siempre activa
  con tareas programadas, pero es **headless: sin escritorio gráfico**. La
  automatización de navegador con renderizado no vive ahí.
- Sus fallos más reportados son clics mal inferidos, timeouts y bloqueos anti-bot.

**Consecuencia de diseño:** Manus es bueno en la capa de *descubrimiento, puntaje,
briefs y reporte*. No es el lugar correcto para la capa de *publicación*. Por eso
el sistema está partido así y la publicación queda en cola con confirmación humana
o en un programador ya auditado (Metricolor/Buffer/Later/Postiz).

---

## 5. Checklist antes de invertir tiempo

- [ ] Tu cuenta de TikTok/Instagram está **enlazada en la plataforma de la campaña**
      (si no, las vistas no se rastrean y no te pagan).
- [ ] Confirmaste el **método de retiro disponible para México** en tu cuenta.
- [ ] La campaña entrega **material propio** y autoriza el uso por escrito.
- [ ] Leíste el **tope por clip** y el **umbral mínimo**, no solo el CPM.
- [ ] El pool está **<80% consumido**.
- [ ] La campaña **no** es de apuestas, cripto sin registro, salud con claims ni adulto.
- [ ] No te cobraron nada por entrar.
- [ ] Tienes dónde guardar capturas de analíticas: son tu evidencia en una disputa.

---

*Esto no es asesoría legal ni fiscal. Para operar a volumen en México revisa el
régimen fiscal aplicable a ingresos por servicios digitales (RESICO / actividad
empresarial) y la obligación de emitir CFDI.*
