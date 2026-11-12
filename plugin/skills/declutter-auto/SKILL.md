---
name: declutter-auto
description: Automatic distillation of the decision cards left by /declutter-reorg. Fills every pending card strictly from its verbatim literal, checks each one with `distill-check` (up to 3 rounds), then launches `declutter reorg distill-auto <id> --json` once and relays the engine's report as is. The batch lands on a new git branch; it never merges, pushes or relaxes the ratchet, and one command undoes the whole batch. Use after /declutter-reorg when the user wants every card distilled in one go, or runs /declutter-auto.
argument-hint: "[-C DIR]"
---

# declutter-auto

Destilado automático de todas las fichas de decisión que dejó `/declutter-reorg`. La IA de la
sesión llena las fichas solo desde el literal; el motor prueba cada una, aplica las que quedan
listas en una rama nueva y escribe el informe. Fusionar sigue siendo decisión del humano.

## Preflight

Run `command -v declutter`. If it is missing, reply with exactly one install line and stop:

`pipx install git+https://github.com/nodo-salud-lab/declutter` (not on PyPI yet)

Add `-C DIR` to every command below if the user passed one.

## Before starting

1. Run `declutter reorg status` to get the run id (`<id>`, the first word of each run line). The
   run must be `committed`; otherwise tell the user to finish `/declutter-reorg` first and stop.
2. Run `declutter reorg distill-check <id> --json`. Exit code 5 means a secret was detected:
   report it by file and line only and stop. Codes 1 and 2: relay the issues and stop. Exit code 4
   is normal while cards are still `PENDIENTE`.
3. Tell the user, in one short message, how many cards are pending and that this mode fills them
   all without asking card by card. Running `/declutter-auto` is the user's OK for filling the
   cards and launching `distill-auto` once; if the user prefers to review each card, point to
   `/declutter-distill`.

## Modo auto

Las reglas de redacción son las mismas de `/declutter-distill` (paso 5): solo desde el literal,
«no consta» cuando no lo dice, anclas y clases deónticas completas, cada número, fecha, ruta e
identificador copiado tal cual, nunca debilitar una regla para que pase. La lectura sigue limitada a
solo la ficha y su bloque del historial (entre los marcadores `declutter:src` y `declutter:end` de
su unidad). Nunca abrir `CLAUDE.md`, `AGENTS.md` ni otras fichas, y nunca pegar el literal en la
conversación.

Tiempo 1, por cada ficha pendiente en orden de `D-NNN`, sin pedir confirmación ni mostrar el diff:
1. Leer la ficha y su bloque del historial, llenar las cinco secciones (`## Qué`, `## Por qué`,
   `## Alternativa descartada`, `## Trampa`, `## Invariante para AGENTS.md`; en inglés `## What`,
   `## Why`, `## Rejected alternative`, `## Pitfall`, `## Invariant for AGENTS.md`).
2. `declutter reorg distill-check <id> --card D-NNN --json`. Si no queda `ready`, corregir y repetir, como máximo 3 rondas.
3. Avisar una línea, sin citar texto de la regla: «D-005 (3/12): lista, −410 B» o «D-007: no pasó (anchor_missing), se salta». Una ficha que no queda lista tras 3 rondas se deja como está y se pasa a la siguiente.

Si el usuario dice «para» durante el tiempo 1, detenerse ahí: no correr el tiempo 2; las fichas llenadas quedan sin aplicar.

Tiempo 2, una sola vez:
- `declutter reorg distill-auto <id> --json`
- Relayar el informe tal cual (aplicadas, saltadas, `AGENTS.md` antes y después, trinquete, comandos), sin agregar ni quitar nada. El programa avisa cada ficha y se frena con Ctrl+C (o Esc en Claude Code).

La skill no fusiona ni ofrece fusionar por su cuenta: el comando de fusionar está en el informe y lo corre el humano. Para deshacer el lote: `declutter reorg distill-undo <id> --auto <n>`.

## Outputs

- With `--json`, stdout carries only the report; notices go to stderr.
- Exit codes: 0 ok, 1 usage or precondition, 3 ratchet, 4 failed proof, 5 secret, 130 stopped
  by Ctrl+C or SIGTERM. For any code other than 0, relay the issues by their key; do not work
  around them.

## Rules

- Read only each card and its unit's history block; never paste literal text into the
  conversation.
- Never invent: «no consta» when the literal does not say it.
- Never `merge`, `push`, delete branches or tags, relax the ratchet or edit `.declutter/` files by
  hand. If `distill-auto` exits 3, say that relaxing the maximum is the human's decision
  (`ratchet relax`, in a TTY).
- Never add numbers of your own to the report: relay the engine's figures only.
