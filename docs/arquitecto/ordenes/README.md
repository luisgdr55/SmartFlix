# Órdenes del Arquitecto — índice

Borradores redactados el 27/11/2026 tras la auditoría general del sistema. **Ninguna está aprobada ni iniciada.** Cada una se construye solo con veredicto del Orquestador (APROBADO · APROBADO CON AJUSTES · MODIFICAR · DETENTE).
La evidencia detallada vive en el informe privado del Arquitecto, fuera del repo (el repositorio es público y el informe describe fallos todavía sin corregir).

| Orden | Tema | Riesgo | Rama | Depende de |
|---|---|---|---|---|
| [O01](o01-seguridad-autenticacion-panel.md) | Seguridad y autenticación del panel (usuarios, roles, sesiones, MFA, auditoría) | 🔴 | `s22-seguridad-auth` | S-001 |
| [O02](o02-pagos-estados-checkout.md) | Orden de pago multi-servicio, máquina de estados, historial de pagos, saneamiento | 🔴 | `s23-pagos-estados` | O01 Lote 0 |
| [O03](o03-rendimiento-rediseno-panel.md) | Rendimiento y rediseño del panel | 🟡/🔴 | `s24-panel-rendimiento-diseno` | O01; O02 (parte de pagos) |
| [O04](o04-pwa-cliente-api.md) | API de clientes y PWA | 🔴 | `s25-pwa-cliente` | O01, O02, O03 |

## Orden de ejecución recomendado
O01 Lote 0 → (en paralelo) O02 Lotes 0 a 3 y O03 Lotes 0 a 1 → O01 Lote 1 → O02 Lotes 4 a 7 → O03 Lotes 2 a 6 → O04.

## Auditor de diseño: Impeccable (decidido el 27/11/2026)
Skill de código abierto (`github.com/pbakaus/impeccable`, Apache-2.0, v4.11.1) que audita y critica diseño de interfaz. Es la puerta de calidad visual de **O03** (panel), **O04** (PWA) y, en lo que aplique, del bot.
- **Instalada en versión reducida y revisada:** solo `SKILL.md` y `reference/`, en `~/.claude/skills/impeccable` del contenedor de la sesión (**no está en este repo**: se pierde al reciclarse el contenedor). Sin hooks, sin binario descargado, sin cuenta ni telemetría.
- **Cómo reinstalarla** (si el contenedor se recicla): clonar `https://github.com/pbakaus/impeccable` en modo lectura, copiar `plugin/skills/impeccable/SKILL.md` y `plugin/skills/impeccable/reference/` (más `LICENSE` y `NOTICE.md`) a `~/.claude/skills/impeccable/`, **sin** la carpeta `scripts/`. Requiere permitir el host `impeccable.style` solo para leer su página, no para instalar.
- **Límite:** sin el motor no hay detección automática por edición ni capturas con su comando `shot`; la crítica y la auditoría se hacen por lectura del código y con capturas de Playwright.
- **Bot de Telegram:** no tiene CSS ni componentes, así que el detector automático no aplica. Se usan sus comandos de redacción y de flujos (`clarify`, `critique`, `harden`) sobre mensajes, teclados y estados vacíos.
- **Pendiente del Orquestador:** ¿habilitar el motor completo (hooks en cada edición y al terminar cada turno, binario de GitHub Releases verificado por SHA-256, telemetría apagada)? Hoy **no**.

## Propuesta pendiente (no redactada)
**O05 — Agente conversacional** (texto y voz) para el bot, apoyado en los servicios de O02 y en la API de O04. Se redacta cuando el Orquestador confirme el alcance.

## Disparadores comunes (CLAUDE.md)
Toda migración de Supabase la ejecuta el Orquestador **antes** de desplegar el código · ninguna dependencia nueva sin aprobación y versión exacta · ningún script contra producción sin autorización expresa por ejecución · fusionar en baja actividad (Railway reinicia el bot).
