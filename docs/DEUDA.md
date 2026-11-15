# SmartFlixVE — Decisiones diferidas con fecha y disparador

> Creado en el rescate r00 (15/11/2026) a partir de `estado_actual.md`, `features.md` y la revisión del repo y de Railway. Estado a esa fecha.

| ID | Decisión diferida | Por qué se pospuso | Disparador | Estado |
|----|-------------------|--------------------|------------|--------|
| S-001 | Decidir **repo público → privado** y, si el historial pudo contener claves, rotar token del bot, Supabase, Gemini, Gmail/IMAP y contraseña del panel | Hallazgo del rescate | **Ya vencido**: antes de la próxima orden | Abierto |
| S-002 | Verificar si el Postgres de Railway se usa; si no, eliminarlo con su TCP Proxy; si sí, cerrar el proxy y rotar la clave | Hallazgo del rescate (requiere acceso a Railway) | Junto con S-001 | Abierto |
| S-003 | Añadir `watchPatterns` en Railway (o separar docs) para que las fusiones de documentación no reinicien el bot | No urgente | Antes de fusionar documentación frecuente | Abierto |
| S-004 | Retirar del repo los `__pycache__/*.pyc` y `.claude/settings.local.json` (`git rm --cached`) | Higiene | Orden de limpieza | Abierto |
| S-005 | Pruebas automatizadas mínimas (flujo de pago, renovación, corte D+7) y CI | Nunca hubo | La próxima orden que toque pagos o scheduler | Abierto |
| S-006 | Confirmar y retirar la carpeta duplicada `streamvip/` | Rebranding sin limpiar | Orden de limpieza | Abierto |
| S-007 | Sweep de `except Exception` demasiado amplios y de funciones que silencian fallos con `return []` / `return {}` (`get_platform_availability`, `get_available_profile_counts`) | Aprendizaje de la sesión 18 | Próximo bug de «error al cargar» | Diferido |
| S-008 | Roadmap de `features.md`: 13 features (broadcast, referidos, reporte semanal, prueba 24h, TMDB, puntos…) y fases 4 a 8 (dashboard web, migración Netflix, soporte, **PWA**, pasarela de pagos) | Alcance futuro | Decisión del Orquestador | Ver `features.md` |
