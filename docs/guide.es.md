# El método de 5 capas

declutter mide y crea una sola convención: **poner cada pieza de conocimiento del proyecto en la
capa donde se carga en el momento justo, al costo justo, y en ningún otro lugar.** Esta guía
explica las capas, cómo decidir dónde va un párrafo y cómo migrar un `CLAUDE.md` inflado,
a mano o con `declutter reorg` (sin pérdida y con prueba de cobertura), y cómo el trinquete impide
que vuelva a crecer.

## Por qué capas

Un agente paga por todo lo que carga al inicio de cada sesión y pierde detalle cada vez que se
compacta la conversación. Un `CLAUDE.md` que crece sin freno mezcla reglas que siempre deben estar
presentes con historia que solo a veces sirve. Las capas las separan según *cuándo deben leerse*.

## Las capas

| Capa | Archivo | Tope por defecto | Carga |
|---|---|---|---|
| 1. Reglas permanentes | `AGENTS.md` (canónico) + `CLAUDE.md` con `@AGENTS.md` más notas solo de Claude | 200 líneas / 12 KiB | Al inicio, en cualquier IA |
| 2. Reglas por zona | `AGENTS.md` en subcarpeta (Codex) + `CLAUDE.md` con `@AGENTS.md` en esa subcarpeta (Claude, bajo demanda) | 150 líneas c/u | Al trabajar en esa zona |
| 3. Estado vivo | `ESTADO.md` / `STATE.md` | 60 líneas | Al inicio (instrucción en `AGENTS.md`; en Claude, opcionalmente un hook) |
| 4. Historia | `docs/historial/AAAA-MM.md` + `INDICE.md` | sin tope | Nunca automático |
| 5. Decisiones y trampas | `docs/decisiones/D-NNN-*.md` + índice, y `docs/trampas.md` | sin tope | Bajo demanda; el índice se lee al iniciar |

`AGENTS.md` es el canónico porque varias herramientas lo leen (Codex de forma nativa). `CLAUDE.md`
lo importa con `@AGENTS.md` para que Claude Code vea las mismas reglas, y agrega solo lo específico
de Claude. `declutter init` crea este esqueleto en español o inglés sin sobrescribir nada.

### Qué va dónde

- **Capa 1: reglas que seguirán siendo ciertas el próximo mes y cuyo olvido cuesta algo.**
  Propósito en dos líneas, cómo instalar/ejecutar/probar, reglas de nombres y estilo que ningún
  linter impone y las invariantes de seguridad (ver abajo).
- **Capa 2: reglas que solo importan en una carpeta o módulo.** Se mantienen junto al código.
- **Capa 3: dónde están las cosas hoy.** Fecha, fase, próximas tareas para la IA, decisiones que
  espera el humano, bloqueos y dónde retomar. Se reescribe, no se le agrega al final.
- **Capa 4: lo que pasó.** Narración fechada, notas por rama, investigaciones resueltas.
- **Capa 5: por qué las cosas son como son.** Un archivo por decisión con su razón y la alternativa
  descartada; y una lista de trampas (cosas que parecen correctas y no lo son).

## Prueba de decisión para cada párrafo

Toma cada párrafo del archivo que estás ordenando y pregunta, en este orden. El primer «sí» decide.

1. **¿Omitirlo causaría daño?** (fuga de datos sensibles, cifras publicadas erróneas, seguridad de
   equipos, acciones irreversibles) -> Capa 1, siempre. Ver la regla de seguridad.
2. **¿Es una regla que aplica en toda sesión y en toda carpeta?** -> Capa 1.
3. **¿Aplica solo dentro de una carpeta o módulo?** -> Capa 2.
4. **¿Es una afirmación sobre el *ahora*** (fase actual, tareas abiertas, bloqueos)? -> Capa 3; si
   ya está obsoleta, va a la capa 4.
5. **¿Es una justificación** («elegimos X sobre Y porque...») **o una trampa?** -> Capa 5.
6. **¿Es un relato de lo que pasó** (entradas fechadas, bitácoras por rama, bugs resueltos)? -> Capa 4.
7. **¿Nada de lo anterior y nadie sabe cuándo se usaría?** Déjalo igual en la capa 4. Nunca se
   borra (ver abajo).

Si un párrafo mezcla dos respuestas, pártelo. Una regla con su historia se convierte en una regla
de una línea en la capa 1 y un enlace a la entrada de la capa 4 o 5.

## La regla de seguridad

Todo lo que cause daño si se omite vive en la **capa 1**: manejo de datos clínicos o personales,
secretos, cifras que se publican, seguridad de equipos o pacientes, acciones irreversibles. No lo
pongas en reglas por carpeta ni en archivos de `.claude/rules/` con `paths:` en el frontmatter: las
reglas limitadas por ruta pueden no recargarse tras compactar la conversación. La capa 1 es la
única que está garantizada delante del agente al inicio de cada sesión. Para saber cómo se carga
cada archivo, consulta la
[documentación de memoria de Claude Code](https://code.claude.com/docs/en/memory.md).

## El contrato del archivo de estado

`declutter radar` y la señal S7 leen el archivo de estado, así que su formato es un contrato. Créalo con
`declutter init` y respeta esta forma (el título en inglés que acompaña a cada sección es un alias aceptado):

```markdown
# Estado — <proyecto>
Actualizado: 2026-11-04
Fase: <una línea>

## Siguiente (IA)         (Next (AI))
- [ ] <tarea> — <fuente>

## Decide el humano       (Human decides)
- [ ] <decisión> — <fuente>

## Bloqueado              (Blocked)
- <qué> — <por qué>

## Dónde retomar          (Resume at)
- <ruta, rama o archivo>
```

Reglas de lectura: el archivo se llama `ESTADO.md` o `STATE.md` y está en la raíz del proyecto; las
líneas de encabezado `Actualizado:`/`Updated:` (la fecha debe ser `AAAA-MM-DD`) y `Fase:`/`Phase:`;
las secciones se identifican por el título o su alias, sin distinguir mayúsculas ni tildes; los
ítems son líneas que empiezan con `- ` en la columna 0 (los sub-ítems con sangría se ignoran);
`[x]` cuenta como hecho; los comentarios HTML se ignoran; una sección ausente no es un error y el
radar la muestra como «—». Reescribe el archivo al terminar cada tarea y mantenlo bajo unas 60
líneas. Un archivo de estado viejo pone S7 en amarillo tras 7 días y en rojo tras 21.

## La historia nunca se borra

Lo que deja de estar vigente pasa a la capa 4; nunca se descarta. Ordenar el contexto es *mover*,
no borrar. Dos prácticas lo mantienen honesto:

- **Destilado más literal.** Cuando condenses una sección larga en una regla corta, conserva el
  texto original en `docs/historial/AAAA-MM.md` y enlázalo desde la regla. El agente lee la versión
  destilada; una persona (o el agente, bajo demanda) puede recuperar el original exacto.
- **Verificación de cobertura.** Antes y después de una reorganización, cada línea del archivo
  antiguo debe encontrarse en algún lugar: en una capa o en la historia literal. Haz commit antes
  de empezar para que `git diff` sea tu prueba. (`declutter reorg` automatiza esta prueba línea a línea.)

## Sesiones: una sesión, una tarea

Las sesiones largas se compactan muchas veces y cada compactación pierde detalle (la señal S6 las
cuenta). Prefiere sesiones cortas con una sola tarea: empieza leyendo el archivo de estado y
termina actualizándolo. Lo que lleva el trabajo de una sesión a otra es el archivo de estado, no la
conversación. Cuando una sesión se haya compactado más de 5 veces, ciérrala, actualiza `ESTADO.md`
y empieza otra.

## Migrar a mano un CLAUDE.md inflado (antes de la fase 2)

> Desde la versión 0.2.0, `declutter reorg` hace esta migración por ti, sin pérdida y con prueba:
> ver [Reorganizar sin pérdida](#reorganizar-sin-pérdida-declutter-reorg). Estos pasos quedan como
> referencia del método.

Hazlo en una rama, con el archivo bajo control de versiones.

1. **Mide.** `declutter measure` y anota S1, S2 y S4. Haz commit del estado actual:
   `git commit -am "chore: snapshot before context cleanup"`.
2. **Crea el esqueleto.** `declutter init --dry-run` y luego `declutter init`. No toca tu `CLAUDE.md`
   existente; crea `AGENTS.md`, el archivo de estado y los índices si faltan.
3. **Archiva el original tal cual.** Copia el `CLAUDE.md` antiguo a `docs/historial/AAAA-MM.md`
   sin cambios. Desde ahora nada se puede perder.
4. **Clasifica párrafo por párrafo** con la prueba de decisión, moviendo texto (sin reescribirlo)
   a su capa: reglas a `AGENTS.md`, reglas de zona al `AGENTS.md` de la subcarpeta, el estado de
   hoy al archivo de estado, entradas fechadas al historial, decisiones justificadas a archivos
   `D-NNN` y trampas al archivo de trampas. Agrega una línea por decisión al índice de decisiones.
5. **Reemplaza `CLAUDE.md`** por `@AGENTS.md` más las pocas notas solo de Claude. Deja las reglas
   de seguridad en `AGENTS.md`.
6. **Verifica la cobertura.** Usa `git diff` y busca frases distintivas del archivo antiguo para
   confirmar que cada una vive ahora en algún lugar. Si un párrafo no tiene hogar, va a la historia.
7. **Mide de nuevo.** `declutter measure` debería mostrar S1 y S4 en verde. Haz commit.
8. **Avísale al agente.** Asegúrate de que `AGENTS.md` instruya leer primero el archivo de estado y
   el índice de decisiones, y actualizar el archivo de estado al final de cada tarea.

## Reorganizar sin pérdida (`declutter reorg`)

`declutter reorg` hace de forma determinista lo que la sección anterior describe a mano: reparte
`CLAUDE.md` (y `AGENTS.md`, si existe) en las 5 capas sin perder un solo byte, y lo demuestra antes
de tocar el repositorio. No resume ni reescribe texto: cada línea original queda en exactamente un
destino, literal, y las reglas de seguridad quedan además copiadas tal cual en `AGENTS.md`.

1. **Plan sin escribir nada.** `declutter reorg plan --dry-run` muestra la tabla de unidades (título,
   líneas, bytes, destino propuesto, copias de seguridad, confianza y regla), la proyección antes y
   después (S1–S4, % de Codex por cadena, archivos que Claude carga al iniciar) y las advertencias.
   Si detecta un secreto, sale con código 5 y no escribe nada.
2. **Plan guardado.** `declutter reorg plan` escribe el manifiesto en `.declutter/reorg/<id>/`
   (ignorado por git). `declutter reorg guide` hace todo el recorrido con un menú en la terminal.
3. **Tus decisiones.** Ninguna unidad con una regla imperativa sale de la capa 1 sin que la
   aceptes: `declutter reorg set <id> <unidad> --accept --reason "<motivo>"`. Para cambiar un destino,
   `--dest L1|L1c|L2:<dir>|L4|L5t|L5m`; para dejarla donde está, `--keep`. Una unidad cuyo título
   tiene léxico de seguridad queda entera en `AGENTS.md` y no se puede mover. `set` sale con 0
   cuando guarda la anulación aunque otras unidades sigan pendientes (lo indica con «quedan N
   confirmaciones pendientes: …»); sale con 4 si la anulación misma no es válida.
4. **Validar.** `declutter reorg check <id>` revalida el manifiesto (código 4 si algo no cuadra).
5. **Aplicar.** `declutter reorg apply <id>` crea la marca `declutter/pre-reorg-<id>` y la rama
   `declutter/reorg-<id>`, escribe los archivos y vuelve a ejecutar la prueba de no pérdida. Con
   `--commit` hace el commit en esa rama. Nunca hace merge ni push: fusionar es tu decisión
   (`git switch <base> && git merge declutter/reorg-<id>`).
6. **Verificar o deshacer.** `declutter reorg verify <id>` repite la prueba cuando quieras;
   `declutter reorg undo <id>` devuelve las fuentes a su estado anterior sin borrar la rama ni la
   marca; `declutter reorg status` lista las corridas; `declutter reorg prune <id>` borra las copias
   literales locales cuando ya no hagan falta.

Códigos de salida: `0` bien, `1` uso o precondición de git, `2` fuente no procesable, `3` presupuesto
de Codex sobre el 100 % o fallo del trinquete, `4` plan inválido o prueba fallida, `5` secreto
detectado. Si el plan sale con 3, lista las palancas posibles con sus bytes (revisar unidades de
confianza baja, marcar falsos positivos de seguridad, subir el presupuesto de Codex, destilar las
fichas de decisión (fase 2b, ver [Destilar las fichas de decisión](#destilar-las-fichas-de-decisión-fase-2b)),
revisar una unidad protegida) y no elige ninguna.

**Presupuesto de Codex del proyecto.** Codex lee la cadena de `AGENTS.md` hasta
`project_doc_max_bytes` (32 KiB por defecto, compartidos por toda la cadena: pasado el límite corta
el archivo raíz y descarta los `AGENTS.md` de zona). Ese valor se puede subir solo para el proyecto
con un `.codex/config.toml` en su raíz, que Codex respeta **solo si el proyecto es de confianza**
(Codex o la extensión de VS Code lo preguntan la primera vez que lo abres; queda como
`[projects."<ruta>"] trust_level = "trusted"` en `~/.codex/config.toml`). Si eliges la palanca
`lever_codex_budget`, el plan muestra el comando concreto:

```sh
declutter reorg codex-budget -C <carpeta> --bytes 81920   # sin --bytes: 65536
declutter reorg codex-budget -C <carpeta> --dry-run       # muestra qué escribiría
```

Escribe `project_doc_max_bytes = N` en `.codex/config.toml` (lo crea si no existe) y
`codex_budget_bytes = N` en `declutter.toml`, cambiando solo esa clave y dejando intacto el resto
de cada archivo; rechaza con código 1 un TOML que no se puede leer o un enlace simbólico. Nunca
toca `~/.codex`: al final imprime el bloque de confianza para que lo agregues tú si hace falta. Si
git ignora `.codex/config.toml`, avisa (Codex lo lee igual, pero no queda en el commit). El
`--bytes` que sugiere el plan es el menor múltiplo de 16 KiB que cubre la cadena más larga
proyectada más un 10 %, con un mínimo de 64 KiB.

En Claude Code, `/declutter:declutter-reorg` recorre estos pasos trabajando solo con el JSON del plan y
pidiendo tu OK antes de cada escritura, del commit y de cada decisión.

### Destilar las fichas de decisión (fase 2b)

`apply` deja cada regla de seguridad literal en `AGENTS.md` (sección «Invariantes de seguridad
(literal, pendiente de destilar)») y, para las unidades que fueron a la capa 4, una ficha de
decisión en `docs/decisiones/D-NNN-<slug>.md` (`docs/decisions/` en inglés) cuyas secciones dicen
`PENDIENTE`. Destilar una ficha es llenarla desde su literal y reemplazar **todas** sus copias
literales en `AGENTS.md` por su «Invariante para AGENTS.md»: una entrada más corta bajo
«Invariantes de seguridad (destiladas)», con enlace a la ficha y al literal del historial. Nada se
pierde: el literal sigue en `docs/historial/` con su sha y en los extractos de la ficha. Las copias
de unidades sin ficha (`no_card`) quedan literales.

1. **Llenar la ficha** solo desde su literal (en Claude Code lo hace `/declutter:declutter-distill`, una
   ficha por vez): «no consta» cuando el literal no lo dice; nunca contenido inventado.
2. **Revisar.** `declutter reorg distill-check <id> [--card D-NNN] [--json]` es de solo lectura. Una
   ficha está `ready` cuando no queda nada pendiente, la invariante conserva cada número, fecha,
   ruta, identificador, URL y span de código de las copias y no agrega ninguno que no esté en la
   unidad, conserva la misma cantidad de cada clase de obligación (`must_not`, `never`, `always`,
   `must`) y el léxico de seguridad de sus categorías, no tiene títulos, importaciones `@ruta`,
   marcadores ni CR, y la entrada es más corta en bytes y no más larga en líneas que las copias que
   quita. Los motivos dan clase, línea y columna, nunca texto. Códigos: `0` todas listas o
   destiladas, `4` alguna no lista, `1` uso, `2` ficha o archivo de control ilegible, `5` secreto.
3. **Aplicar en una terminal.** `declutter reorg distill-apply <id> --card D-NNN` trabaja en la rama
   nueva `declutter/distill-<id>-<D-NNN>` después de la marca `declutter/pre-distill-<id>-<D-NNN>`. Lista lo
   que cambiará y pide un motivo y la palabra `confirmo`; sin TTY sale con 1 sin escribir nada, y
   `--yes` nunca la omite. `--dry-run` construye y prueba sin escribir. Fija la entrada nueva y la
   ficha destilada (roles `invariant` y `card`), agrega un evento `distill` a
   `.declutter/ratchet.log` y, si el destilado baja S1 o el porcentaje de Codex, un evento
   `tighten` que baja los máximos del trinquete (nunca los sube).
4. **Commit** como paso aparte: `declutter reorg distill-apply <id> --card D-NNN --commit` (con `--yes`
   si no hay TTY; solo confirma el commit). Nunca hace merge ni push.
5. **Verificar.** `declutter reorg verify <id> --strict` y `declutter ratchet check` deben dar 0 (estados
   `distilled`, `card_distilled`, `control_appended`).
6. **Deshacer.** `declutter reorg distill-undo <id> --card D-NNN` restaura los bytes previos exactos. Es
   LIFO: solo se deshace el último destilado de la corrida.

La última línea de `distill-apply` y `distill-undo` es `<id> D-NNN <estado>` (`written`,
`committed`, `undone` o `planned`); los errores van a stderr con claves `distill.<clave>`.

**Presupuesto de líneas (R1).** El primer destilado de una corrida agrega una región de 4 líneas
(línea en blanco, título, marcador y cierre); después, ninguna entrada es más larga en líneas que
las copias que reemplaza. Si el proyecto está sobre `S1_max + 1` líneas antes del primer destilado,
`distill-apply` sale con 3 sin escribir nada; subir el máximo es decisión tuya (`declutter ratchet relax`,
confirmado en la terminal).

**Límite.** El destilado solo no deja la capa 1 en 12 KiB: no toca los primarios de la capa 1, que
siguen siendo decisión tuya.

### Destilado automático (fase 3A)

Cuando hay muchas fichas listas, `declutter reorg distill-auto <id>` las aplica todas de corrido, sin
confirmar ficha por ficha. Trabaja en una rama nueva `declutter/distill-auto-<id>-<n>`, después de la
marca `declutter/pre-distill-auto-<id>-<n>`, con un commit por ficha. Es un flujo de dos tiempos:

1. **Llenar.** En Claude Code, `/declutter:declutter-auto` llena y revisa cada ficha (hasta 3
   rondas por ficha, solo desde su literal) y deja sin tocar las que no quedan listas.
2. **Aplicar.** La skill lanza `declutter reorg distill-auto <id> --json` una sola vez; también puedes
   correrlo tú. Nunca lee la entrada estándar ni pide confirmación: avisa una línea por ficha.
   `--skip D-NNN` deja una ficha fuera y `--dry-run` corre todo en una copia temporal.

**Frenar.** Ctrl+C (o la señal de término) detiene el lote de forma limpia con código 130: si la
ficha en curso ya empezó a escribirse, se completa; si no, se cancela sin escribir nada. Las ya
aplicadas quedan en la rama y las fichas que no quedan listas quedan editadas pero sin aplicar. Una
ficha que falla se restaura y se salta; un secreto, una edición concurrente o una condición global
fallida detienen el lote. El
trinquete nunca se relaja solo: si sale con 3, subir el máximo sigue siendo decisión tuya.

**Informe.** Queda en `.declutter/distill/<id>/auto-<n>/informe.md` (sello GUÍA), armado solo desde
los journals y las mediciones: fichas aplicadas y saltadas, `AGENTS.md` antes y después, trinquete y
los comandos para fusionar y para deshacer. Con `--json`, stdout lleva solo ese informe en JSON y
los avisos van por stderr.

**Fusionar y deshacer.** El motor nunca fusiona ni hace push: el comando de fusionar viene en el
informe y lo corres tú. Para deshacer el lote completo: `declutter reorg distill-undo <id> --auto <n>`
(`--card` y `--auto` son excluyentes). Si el lote no se fusionó, vuelve a la rama base; si ya se
fusionó, pide una terminal y la palabra de confirmación, y revierte sus commits. Al deshacer, las
fichas llenadas vuelven al estado llenado que tenían antes del lote (modificadas sin commit), así que
no se pierde el trabajo de la IA y puedes relanzar con `--skip D-NNN`. Si alguien editó una ficha
después, no se toca y se avisa con una línea que nombra la ficha.

## Trinquete

Después de reorganizar, `declutter reorg apply` deja una línea base en `.declutter/ratchet.json` y las
fijaciones de las copias de seguridad en `.declutter/pins.json` (los dos van en el commit).
`declutter ratchet check` compara el proyecto con esa línea base y sale con código 3 si `CLAUDE.md` y
`AGENTS.md` crecen por encima del máximo, si el presupuesto de Codex sube, si vuelve a aparecer un
título con forma de bitácora, si un `AGENTS.md` de zona se pasa de líneas, si una copia fijada
cambió o si un literal del historial, de `docs/trampas.md` o de `docs/mapa.md` fue alterado. Cada
fallo trae su remedio. Los máximos solo bajan; para subir uno a propósito existe
`declutter ratchet relax`, y para soltar o actualizar una fijación, `declutter ratchet unpin` y
`declutter ratchet repin`. Los tres piden confirmación en la terminal y quedan en
`.declutter/ratchet.log`. Desde la 0.3.0, `declutter reorg verify <id> --strict` acepta archivos de
control que solo recibieron eventos auditados (`control_appended`): después de un
`declutter ratchet unpin` o de un destilado sale con 0 en vez de 4. Un `ratchet.json` cambiado por
`relax` sigue informándose como `control_edited`.

**Uso manual primero.** Ejecuta `declutter ratchet check` antes de cada commit (`--staged` revisa lo
que está en el índice). La herramienta no instala ningún hook.

**Formateadores y finales de línea.** Los literales del historial se verifican byte a byte. Te
recomendamos, como decisión tuya, excluir `docs/historial/**` (o `docs/history/**`),
`docs/trampas.md` y `docs/mapa.md` de los formateadores y hooks que reescriben Markdown
(`trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending`, prettier sobre `*.md`), y, si tu
repositorio normaliza finales de línea, marcar esas rutas con `-text` en `.gitattributes`.

**`.gitattributes` con `* text=auto eol=lf`.** `plan` y `apply` ya no se
detienen por esa línea (ni por `core.autocrlf=input`) cuando ningún archivo involucrado tiene
CRLF ni ningún byte `\r`: git no cambia esos bytes en ningún sentido. Verás el aviso
`git_normalizes_lf_safe` con la ruta y el atributo. Con `eol=crlf`, `core.autocrlf=true`,
`text=auto` sin `eol`, filtros o contenido con `\r`, siguen saliendo con 1.

**Hook opcional.** Si más adelante quieres que git lo compruebe solo, crea a mano
`.git/hooks/pre-commit` (ejecutable) con:

```sh
#!/bin/sh
exec declutter ratchet check --staged
```

En CI, el paso equivalente es `declutter ratchet check`, que sale con 3 si falla, igual que
`declutter measure --fail-on red`.

## Mantenerlo ordenado

- Corre `declutter measure --fail-on red` en CI, o agrega la barra de estado y el hook `SessionStart` del
  README, para que los problemas aparezcan antes de costar algo.
- Usa `declutter radar` cada semana para ver qué repositorios tienen el archivo de estado viejo o ausente.
- Ajusta los umbrales en `declutter.toml` si tu proyecto de verdad necesita más espacio, pero hazlo
  a propósito y deja escrito el porqué en un archivo de decisión.
