# O02 Lote 7 — Saneamiento de datos existentes (herramienta; NO ejecutada)

Rama `claude/keen-mendel-vn6j7b`. **No se ejecutó nada contra producción.** Se entrega la herramienta; cada ejecución requiere tu autorización expresa.

## Qué se construyó
- `services/saneamiento.py` (lógica pura) y `scripts/o02_saneamiento.py`.
- **Por defecto solo propone** (`--dry-run` implícito): lee `subscriptions` y `profiles`, imprime solo ids cortos (8 caracteres), plataforma, estado, plan y fecha (sin nombres, teléfonos ni credenciales) y, con `--out`, guarda la propuesta en un JSON **fuera del repo** (contiene UUID; `.gitignore` lo excluye).
- Aplicar exige `--apply --plan … --approved … --admin-id … --confirm EJECUTAR`. `ok.json` lista, por categoría, los ids cortos aprobados **fila por fila**; un id desconocido, ambiguo o de categoría no aplicable aborta todo sin escribir.

## Categorías
| Categoría | Criterio | Acción propuesta |
|---|---|---|
| `reset_avisos` | `active` vigente con `expiry_notified`, `debt_reminder_count>0`, o `reminder_sent` con fin a más de 4 días | reiniciar avisos |
| `expired_libre` | `expired` heredado con perfil libre, sin perfil, o usado por otro cliente | `cancelled` |
| `expired_ocupado` | `expired` con el perfil aún ocupado | **no se toca** (queda como mora; informativo) |
| `express_fuga` | express vencido hace >24 h con el perfil ocupado | liberar perfil (PIN nuevo, misma regla del corte) y `cancelled` |

Fusiones y anulaciones de duplicados **no** están aquí: ya se hacen con auditoría en la pantalla «Anomalías» del panel (Lote 5).

## Garantías al aplicar
UPDATE condicional por fila (si cambió desde la propuesta, se omite), se omiten clientes con pago pendiente, `release_profile_for_cut` no libera un perfil ocupado por otro, auditoría en `admin_log` y archivo `…deshacer.json` con los valores previos (el PIN anterior no se restaura). Fuera del alcance: borrar filas, cambiar esquema, tocar `streamvip/`.

## Verificado y no verificado
- 350 pruebas OK (7 nuevas): clasificación, informe sin datos personales, aprobación estricta, dry-run sin escrituras, rechazo sin confirmación, aplicación solo de lo aprobado, omisión por pago pendiente y por fila cambiada. Mutación comprobada (quitar el guard de estado hace fallar la prueba).
- **No verificado:** ejecución real contra Supabase (el cliente `range()` paginado y el comportamiento de `release_profile_for_cut` con datos reales).

## Cómo se usaría (cuando lo autorices)
1. Tú ejecutas el modo propuesta con tus credenciales y me pasas solo los conteos por categoría.
2. Decides categoría por categoría y rellenas `ok.json`; 3. aplicas por categorías, de menos a más riesgo (`reset_avisos` → `expired_libre` → `express_fuga`), revisando entre una y otra.
4. Recomiendo hacerlo **después** de ejecutar la migración y de probar el flujo de punta a punta, y en baja actividad.

## Preguntas
1. ¿Autorizas ejecutar la propuesta (solo lectura) contra producción, y quién la corre?
2. ¿Los `expired_ocupado` se dejan como mora (recomendado) o se cierran a mano?
