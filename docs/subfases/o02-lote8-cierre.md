# O02 Lote 8 — Documentación de cierre

Hecho: `CLAUDE.md` (guarda del corte reescrita: no hay corte automático, mora derivada, fechas de Caracas, reclamo atómico, flag), `docs/ESTADO.md` (§0 y tabla de módulos), `docs/FLUJOS.md` (nuevo; la orden lo citaba y no existía), `features.md` (Fase 8). Solo documentación; no se tocó código.

## Estado final de O02 (todo en la rama `claude/keen-mendel-vn6j7b`, sin fusionar)
Lotes 0 a 8 entregados. 350 pruebas `unittest` OK; nada verificado contra Telegram, Supabase, Redis, Gemini, Railway ni navegador reales.

## Secuencia recomendada para llegar a producción (decide el Orquestador)
1. Revisar el PR (abrirlo solo cuando lo pidas) y releer `docs/migraciones/o02-lote2-checkout.sql`.
2. Ejecutar la migración en Supabase (tú) y **luego** fusionar en baja actividad con `CHECKOUT_V2_ENABLED=false`; vigilar logs de Railway. Al fusionar, con el flag apagado, ya cambian: fin del corte D+7, textos de aviso y resumen de mora.
3. Probar de punta a punta con tu cuenta; después encender el flag (variable no secreta, solo con tu orden) y vigilar.
4. Con datos reales y tu autorización: propuesta de saneamiento (solo lectura) y aplicar por categorías.

## Pendientes fuera de O02
`/renovar` por el motor, reactivar `cancelled`, selector de periodos de renovación, política de imágenes duplicadas, `send_to_admins` inexistente, rama remota `fix/o02h-guardas-pagos-perfiles`, limpieza de `streamvip/` (S-006), rotación de claves (R01-1).
