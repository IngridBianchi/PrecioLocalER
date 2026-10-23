# Demo — PrecioLocal ER (cómo grabarla)

Guía para producir el **video de 90 segundos** (Fase 7) y el GIF del README (Fase 5).

## 1. Storyboard (90 s)

| Tiempo | Escena | Qué se muestra | Narración sugerida |
|---|---|---|---|
| 0–10 | Hook | Dashboard con skeleton y aviso de datos de demostración | "En Argentina con inflación de tres dígitos, comparar precios es una tarea diaria." |
| 10–25 | Buscar | Buscar "aceite" → resultados con cadena y fecha de captura | "Busquemos aceite en las cadenas de Entre Ríos." |
| 25–40 | Detalle | Histórico + banda de predicción (2 modelos) | "Cada producto tiene su historia y su predicción a 7 días." |
| 40–55 | Comparar | Comparador del mismo producto entre cadenas | "La misma canasta, en todas las cadenas, al mismo tiempo." |
| 55–70 | Alertas | UI de alertas y banner de notificación en vivo (backend en stub) | "Que la app vigile y te avise cuando cambie." |
| 70–85 | Cierre | PWA instalable + stack + link | "PWA instalable, 4 backends, 3 bases, sin pagar un peso." |

## 2. Antes de grabar

- Precalentar los servicios (evitar cold start a mitad de video): abrir `/health` de cada servicio 1 min antes.
- Usar datos DEMO (`./scripts/pl.sh e2e-data`) para tener comparables entre cadenas.
- Resolución 1920×1080; sin datos personales de dev.
- Música opcional; subtítulos siempre.

## 3. Mejor tooling

- Grabación: Loom / OBS / ShareX.
- Cortes: `ffmpeg` o CapCut (web).
- GIF del README: `ffmpeg -i demo.mp4 -vf "fps=10,scale=960:-1" demo.gif`.

## 4. Checklist de señales a mostrar

- [ ] Fecha/hora visible (datos no simulados cuando se usan reales).
- [ ] Latencia real (skeleton visible).
- [ ] Notificación de alerta llegando en vivo (WebSocket), si el backend está disponible.
- [ ] PWA instalable en desktop / Android.
- [ ] Logo/README en el cierre.

## 5. Contenido de apoyo (sin video)

- GIF de 3 flujos para el README.
- 3 screenshots (Dashboard, Detalle, Comparador).
- Medallas Lighthouse > 90.

## Archivo de referencia

Este doc se ejecuta en Fase 7 (video) y Fase 5 (GIF/screenshots). El enlace final vive en el README.
