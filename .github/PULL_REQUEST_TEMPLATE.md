## Descripción

¿Qué cambia y por qué? Una frase. La justificación (si no es trivial) va en la descripción, no en el título.

## Tipo de cambio

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor
- [ ] Docs / infra
- [ ] Dependencias

## Cómo se verificó

- [ ] CI en verde
- [ ] Tests nuevos/cambiados (unit/integration/e2e)
- [ ] Comando local ejecutado: `...`
- [ ] Sin secretos en el diff (`gitleaks` corre en CI)

## Decisiones relacionadas

- ¿Toca un ADR? [docs/adr](../docs/adr) (si cambia una decisión de arquitectura, adjuntar el ADR actualizado).

## Screenshots (si aplica)

## Checklist

- [ ] El diff respeta el rol único de la pieza (ver [architecture.md](../docs/architecture.md))
- [ ] No rompe el contrato ([contracts.md](../docs/contracts.md)) o si lo rompe, actualiza el contrato en la misma PR
- [ ] 1 review aprobado antes de merge (disciplina: aunque sea el autor)