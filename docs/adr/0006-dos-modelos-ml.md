# ADR-0006: Dos modelos ML con roles complementarios

## Estado

Aceptado · 2026-09-23 · Actualizado 2026-10-20 (decisión `.[ml]`: Opción B).

## Contexto

El plan original acumulaba tres modelos (SARIMAX + Holt-Winters + IsolationForest
+ SsaForecaster). Acumular modelos no es calidad: cada modelo es costo de
entrenamiento, de operación y de explicación. La pregunta es qué rol cumple cada
uno y si la combinación aporta más que el modelo solo.

La imagen del scraper no instala `statsmodels`, `numpy` ni `scikit-learn`: son el
extra opcional `.[ml]` de `pyproject.toml` y suman ~200 MB. En el free tier eso
no se justifica para series cortas de retail.

## Decisión

**Dos modelos de forecasting con roles complementarios + un detector de
anomalías, todos sin dependencias pesadas:**

1. **Holt-Winters (Python, implementación propia)** — modelo de producción.
   Captura nivel, tendencia y estacionalidad aditiva; con series cortas degrada
   a Holt lineal. Se entrena a diario por (producto, cadena) en
   `scripts/train_forecasts.py`.
2. **SsaForecaster (ML.NET)** — segunda opinión independiente (otra tecnología,
   otro runtime, otro algoritmo). Si ambos coinciden en dirección, la confianza
   sube; si divergen, el comparativo lo muestra en vez de esconderlo.
3. **Robust z-score (mediana + MAD)** — NO es un tercer forecast. Es una
   **feature de calidad de datos**: marca puntos anómalos (errores de scrape,
   crashes de precio) sin borrarlos ni interpolarlos.

### El extra `.[ml]` no es el camino de producción

`SARIMAX` e `IsolationForest` quedan disponibles solo para experimentación local
cuando se instala `.[ml]`. No son el default y no se prometen en la API:

- `GET /ml/forecast/{id}` usa `model=holtwinters` por default. No devuelve
  `degraded_reason` en el camino normal.
- Si alguien pide explícitamente `model=sarimax` sin el extra, el servicio
  devuelve Holt-Winters con `degraded_reason`; nunca reporta como SARIMAX un
  modelo que no corrió.
- `GET /ml/anomalies/{id}` usa robust z-score por default; `IsolationForest`
  solo aparece si scikit-learn está instalado.

Esta es la **Opción B** de la decisión pendiente: aceptar Holt-Winters y robust
z-score como los modelos reales, en lugar de agregar ~200 MB a la imagen para
que SARIMAX e IsolationForest corran en producción.

### Cómo combinamos los resultados

- Un forecast se persiste con el nombre del modelo que **realmente corrió**
  (`model`, no `requested_model`).
- El gateway expone el forecast Python y el comparativo contra .NET; el
  frontend pinta la banda y muestra la degradación si existe.
- La dirección se reporta como consenso solo si los dos modelos coinciden; si
  no, se marca `low_confidence`.

## Alternativas consideradas

| Alternativa | Pros | Contras | Por qué no |
|---|---|---|---|
| Instalar `.[ml]` en la imagen (SARIMAX + IsolationForest) | Modelos más sofisticados | +~200 MB, más tiempo de build y de arranque; series de 4–30 puntos no justifican SARIMAX | No entra cómodo en el free tier y no mejora el contrato |
| Tres modelos "de muro" | Parece más | Costo de operación + sin explicación de roles | La complejidad no se justifica |
| Un solo modelo | Simple | No demuestra comparación de enfoques (el objetivo incluye ML.NET) | No cumple el objetivo del portfolio |
| TensorFlow/PyTorch LSTM | Llamativo | RAM/entrenamiento imposible en free tier; overkill para series cortas | Rechazado por restricción real |

## Consecuencias

- **Positivas:** cada modelo se defiende con una frase; el consenso reduce la
  confianza ciega en un solo algoritmo; robust z-score protege el training de
  dirty data sin agregar dependencias.
- **Negativas:** el forecast Python es más simple que SARIMAX; si en el futuro
  aparecen series largas con estacionalidad fuerte, habrá que revisar esta
  decisión con métricas, no por moda.
- **Neutras:** el comparativo Python vs .NET sigue siendo el valor del ADR. Los
  modelos `.[ml]` se pueden medir localmente, pero no forman parte del contrato
  de producción.

## Referencias

- [ADR-0001-multiples-lenguajes](0001-multiples-lenguajes.md)
- [docs/contracts.md](../contracts.md) — entidad `Prediction` y endpoints `/ml`
- [services/scraper/README.md](../../services/scraper/README.md) — modelos y
  extra `.[ml]`
