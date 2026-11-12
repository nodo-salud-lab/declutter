Antes de trabajar, lee `ESTADO.md` y `docs/decisiones/INDICE.md`. Este archivo es canónico para cualquier agente de IA.

# {{project_name}}

Plantilla creada con declutter el {{date}}. Completa lo marcado con `RELLENAR` y borra esta línea.

## Propósito
- RELLENAR: qué hace este proyecto y para quién, en 2 o 3 líneas.

## Cómo ejecutar y probar
- RELLENAR: comando de instalación.
- RELLENAR: comando para correr el proyecto.
- RELLENAR: comando para correr las pruebas (obligatorio antes de dar una tarea por terminada).

## Invariantes de seguridad
Lo que causa daño si se omite vive aquí y no en reglas por carpeta: estas pueden no recargarse tras compactar.
- RELLENAR: datos sensibles o de personas (qué no sale del entorno controlado).
- RELLENAR: secretos y credenciales (nunca en el repositorio ni en prompts).
- RELLENAR: cifras o resultados que se publican (cómo se verifican).
- RELLENAR: acciones irreversibles (borrar, publicar, enviar): requieren confirmación explícita.

## Zonas
- RELLENAR: carpeta o módulo con reglas propias. Cada zona lleva su `AGENTS.md` (máx. 150 líneas) y un `CLAUDE.md` con `@AGENTS.md`.

## Dónde vive cada cosa
| Capa | Archivo | Tope |
|---|---|---|
| Reglas permanentes | `AGENTS.md` (este) y `CLAUDE.md` | 200 líneas / 12 KiB |
| Reglas por zona | `AGENTS.md` en subcarpetas | 150 líneas c/u |
| Estado vivo | `ESTADO.md` | 60 líneas |
| Historia | `docs/historial/AAAA-MM.md` + `INDICE.md` | sin tope, nunca se carga solo |
| Decisiones y trampas | `docs/decisiones/D-NNN-*.md` + `INDICE.md`, `docs/trampas.md` | sin tope, bajo demanda |

## Reglas para agentes
- Al terminar cada tarea, actualiza `ESTADO.md` (fecha, fase, siguiente, bloqueos, dónde retomar).
- Nunca agregues bitácora ni changelog a `AGENTS.md` ni a `CLAUDE.md`: va en `docs/historial/AAAA-MM.md`.
- Registra cada decisión como `D-NNN` en `docs/decisiones/` con su porqué y la alternativa descartada, y añádela al índice.
- No borres información: lo que ya no sea vigente se mueve al historial.
- No elijas por el responsable humano lo que figure en «Decide el humano».
- Mantén este archivo en 200 líneas o menos. Si crece, mueve detalle a una zona o a `docs/`.
- Ante duda sobre una acción irreversible, detente y pregunta.
