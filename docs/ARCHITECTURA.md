# Arquitectura

## El principio rector

El negocio de clipping se pierde por tres causas, y ninguna es "no hacer suficientes
videos":

1. **Material sin licencia** → strike, suspensión, saldo pendiente perdido.
2. **Publicar en campañas cuyo pool ya se agotó** → las vistas verifican tarde y
   no pagan.
3. **Duplicados y vistas de bot** → filtrados, $0.

Por eso el sistema no está optimizado para producir más. Está optimizado para
**no producir lo que no paga**.

```
                 ┌──────────────────────────────┐
   Manus /       │  1. DESCUBRIMIENTO           │  CSV con contrato fijo
   extensión ───▶│  discovery.py                │  (nunca scraping de sesión)
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  2. PUNTAJE                  │  valor esperado, no CPM
                 │  discovery.puntuar()         │  cpm × log(pool) × %disponible
                 └──────────────┬───────────────┘     × multiplataforma × riesgo
                                ▼
                 ┌──────────────────────────────┐
                 │  3. COMPUERTA (campana)      │  bloquea: CPM < $0.50,
                 │  compliance.revisar_campana  │  pool ≥80%, categoría de riesgo
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  4. ACTIVOS LICENCIADOS      │  solo material de la campaña,
                 │  discovery.registrar_asset   │  con evidencia de la licencia
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  5. BRIEF DE EDICIÓN         │  estructura prestada,
                 │  clip_spec.generar_brief     │  material licenciado
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  5b. RENDER                  │  OpusClip por API;
                 │  providers/                  │  SendShort/CapCut asistido
                 │  + detector de marca de agua │  (no tienen API publica)
                 └──────────────┬───────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  6. COMPUERTA (clip)         │  bloquea: sin licencia,
                 │  compliance.revisar_clip     │  clonado, sin #ad, claims,
                 └──────────────┬───────────────┘  marca de agua, <3s
                                ▼
        ┌───────────────────────────────────────────────┐
        │  7. PUBLICACIÓN — tres modos                  │
        │     cola (defecto) → confirmación humana      │
        │     api_*          → requiere auditoría       │
        │     programador    → Metricolor/Buffer/Postiz │
        └───────────────────────┬───────────────────────┘
                                ▼
                 ┌──────────────────────────────┐
                 │  8. LIQUIDACIÓN              │  verificadas, umbral, tope,
                 │  earnings.liquidar_post      │  comisión, pool restante
                 └──────────────┬───────────────┘
                                ▼
                        REPORTE AL OPERADOR
                  cobrado vs. estimado + 1 acción
```

## Decisiones y su porqué

**El puntaje no es el CPM.** Un CPM de $5 con pool 95% consumido paga menos que
$1.20 con pool fresco, porque las vistas verifican ~5 días después y el pool ya no
está. `puntuar()` multiplica `cpm × log1p(pool/1000) × fracción_disponible ×
factor_multiplataforma × bonus/penalización_de_categoría`.

**El factor multiplataforma existe** porque un mismo clip publicado en 3 redes
elegibles cobra tres veces por el mismo trabajo de edición. Es la palanca de
eficiencia más barata del modelo.

**La compuerta es una función pura, no un decorador.** Devuelve bloqueos y avisos
por separado, y se puede llamar desde la CLI, desde el encolado y desde Manus.
Un bloqueo cambia el estado del clip a `bloqueado` en la base: queda auditable.

**El brief es un artefacto versionado, no una llamada a una API de video.** Las
herramientas de video con licencia comercial y API estable son pocas y cambian
cada pocos meses. Un brief en Markdown con gancho, beats por segundo y notas
sobrevive a ese cambio y es revisable por un humano en 20 segundos.

**La liquidação modela los cinco filtros.** `liquidar_post()` devuelve
`motivo_cero` cuando el clip no paga, con la razón exacta. Sin eso, el operador ve
un número que nunca llega al banco y no sabe por qué.

**`publicar_tiktok()` se niega a ejecutarse si `auditoria_aprobada` es falso.** No
es un comentario: lanza excepción. Publicar en `SELF_ONLY` consume presupuesto de
producción y genera cero vistas, y es mejor que el sistema lo impida a que el
operador lo descubra una semana después.

## Por qué hay tres modos de proveedor y no uno

Porque solo una de las tres herramientas tiene API. Verificado contra documentación
oficial en 2026-09:

- **OpusClip**: API real en `https://api.opus.pro/api`. El help center oficial la
  describe en beta cerrada para planes anuales de alto volumen; el sitio comercial dice
  que empieza en Pro. Ante la contradicción, el conector degrada con un mensaje claro
  en 401/403 en lugar de fallar a medias.
- **SendShort**: sin API pública. No hay endpoint que llamar.
- **CapCut**: sin API de renderizado. Su "Open Platform" es para plugins *dentro* del
  editor; su "AI API" se limita a texto-a-video y plantillas.

Fingir automatización donde no la hay sería peor que admitirlo: el operador esperaría
un pipeline que no existe.

## El detector de marca de agua

La señal útil no es el brillo sino la **estática**: un logo superpuesto no cambia entre
frames, el video de fondo sí. Se muestrean 6 frames y se buscan pixeles con varianza
temporal ≈ 0, brillo alto y saturación baja en las cuatro esquinas.

Verificado con video real generado frame a frame: **0.0%** sin marca, **22.6%**
localizado en `inf_der` con la marca puesta ahí.

Límite conocido y documentado: con un fondo completamente estático y claro da falso
positivo (lo reproduce `testsrc`). Por eso no bloquea en silencio: deja el clip en
`revision_agua` y el operador decide con `--forzar`.

## Qué es deliberadamente manual

| Paso | Por qué |
|---|---|
| Publicar | Auditoría de plataforma + filtro anti-bot de la campaña |
| Descargar material | Confirmar que la licencia es real, no asumida |
| Aprobar campaña | Juicio sobre la marca y su historial de pago |
| Enviar la URL a la campaña | Es el hecho que activa el pago |
| Renderizar el video | Herramientas externas, cuentas y cuotas propias |

## Extensión

Para agregar una plataforma de campañas nueva: implementar una función que devuelva
el mismo CSV de 12 columnas y llamar a `discovery.importar_csv()`. Todo lo demás
(puntaje, compuertas, liquidación, reporte) funciona sin cambios, porque nada
aguas abajo depende de la plataforma de origen salvo el porcentaje de comisión en
`earnings.COMISION_PLATAFORMA`.
