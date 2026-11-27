# O03 — Rendimiento y rediseño del panel de administración · 🟡 (🔴 en la migración de índices y funciones) · rama `s24-panel-rendimiento-diseno`

**Estado:** BORRADOR para veredicto del Orquestador (27/11/2026) · **Depende de:** O01 (sesiones, CSRF, auditoría); la parte de pagos y ficha de cliente depende de O02 · **Precede a:** O04 (comparte tokens de diseño)
**Evidencia:** informe privado del Arquitecto (mediciones en navegador real, escritorio y móvil).

## Objetivo
Un panel que abra en menos de un segundo, se vea y funcione bien en el teléfono, diga la verdad sobre el estado del sistema y le permita al admin resolver cualquier caso (por ejemplo, un cliente con varios servicios y pagos pendientes) sin salir de una pantalla.

## Línea base medida (27/11/2026, producción)
| Página | Primer contenido visible |
|---|---|
| Dashboard | **5,2 s** escritorio · 4,2 s móvil |
| Cuentas | 3,8 s · 4,1 s |
| Suscripciones, detalle de cliente, ajustes, migrar | 1,7 a 2,1 s |
| Clientes, pagos, plataformas | 0,8 a 1,1 s |
Causas: consultas a Supabase síncronas y en serie dentro de handlers `async` (cada una frena también el bot), unas 8 a 19 consultas por carga del dashboard, N+1 en cuentas y pagos, un cliente Redis nuevo por llamada, escritura en base dentro de GET, sin compresión, Tailwind compilado en el navegador, páginas de 158 KB sin paginar.

## Presupuestos de rendimiento (criterio de cierre)
- Dashboard: estructura visible < 600 ms; datos completos < 1,5 s en frío y < 800 ms con caché.
- Listas: servidor p95 < 800 ms, paginadas siempre.
- HTML por página < 100 KB sin comprimir · CSS propio < 50 KB · JS inicial < 200 KB comprimido.
- Móvil (4G irregular, gama media): primer contenido < 2,5 s.
- Ningún GET escribe en la base.

## Dirección de diseño
La base actual (tarjetas, barra lateral, KPIs) es sólida y **se evoluciona, no se rehace**. Se corrige lo que está roto y se sube el nivel:
1. **Tokens** en un solo archivo (colores con contraste AA en tema claro, escala tipográfica, espaciado, radios, sombras, duraciones). Se elimina la mezcla de clases de tema oscuro sobre fondo claro.
2. **Dirección de Diseño** de una página en `docs/DISENO.md`, aprobada por el Orquestador **antes** de la primera pantalla (ATLAS §9). Tono: herramienta de operación rápida y confiable, densidad media, cifras tabulares en dinero.
3. **Prioridad sobre decoración:** el dashboard pasa de tres banners siempre encendidos a un bloque **«Requiere acción hoy»** ordenado por urgencia (pagos por revisar, mora, anomalías, servicios que vencen) con la acción en cada fila.
4. Estados humanos en todo el panel (mapa único de estados internos a etiquetas y colores; nunca un valor técnico a la vista).
5. Móvil primero: tablas que en pantalla angosta se convierten en tarjetas con la acción principal visible; objetivos táctiles ≥ 44 px; etiquetas accesibles en todo botón con ícono.
6. Retroalimentación: avisos tipo «toast» en vez de parámetros en la URL y `alert()`; confirmación solo para lo irreversible, con **deshacer** donde sea posible; botones deshabilitados mientras envían y siempre rehabilitados; estados vacío/cargando/error con acción.

**Skill de diseño:** se pidió usar `impeccable`; **no aparece en las skills de la cuenta** (verificado el 27/11/2026: solo `atlas` y `video-campana-ia`). Si el Orquestador la instala, se aplica en el Lote 3; mientras tanto rige la doctrina de ATLAS §9 y la skill de diseño de artefactos para los prototipos.

## Alcance

### Lote 0 — Tokens y Dirección de Diseño (solo documentos y un archivo de tokens)
Propuesta visual para aprobación con maqueta del dashboard y de la ficha de cliente en escritorio y móvil.

### Lote 1 — Infraestructura de rendimiento (sin cambio visual)
- Capa de acceso a datos no bloqueante: `asyncio.to_thread` alrededor de las llamadas síncronas de supabase-py (se verifica en la documentación oficial si la versión instalada ofrece cliente asíncrono antes de decidir).
- Un único cliente Redis reutilizado; la caché del dashboard guarda el resultado completo, con *stale-while-revalidate* e invalidación por versión en toda escritura relevante.
- Eliminar escrituras en GET (el barrido de vencimientos) y los N+1 (conteos por cuenta, perfiles por plataforma, búsqueda por pago).
- Paginación del servidor en cuentas, perfiles, pagos y plataformas; búsqueda con escape de comodines y de separadores del filtro.
- `GZipMiddleware` (incluido en Starlette), cabeceras de caché para estáticos, Chart.js solo en el dashboard, Alpine con versión fija y su archivo propio.
- Migración 🔴 (la ejecuta el Orquestador): índices en `subscriptions(user_id, platform_id, status)`, `subscriptions(status, end_date)`, `profiles(platform_id, status)`, `users(created_at)`; función SQL `dashboard_stats()` que devuelve todos los totales en una consulta.

### Lote 2 — Dashboard rápido
Estructura inmediata y widgets que cargan en paralelo desde endpoints JSON existentes o nuevos, cada uno con *skeleton*, error con «Reintentar» y tiempo de espera. Se redefine «Cuentas por vencer» (fecha de cobro de la cuenta maestra) con un dato que se mantenga o se retira. Gráfica de ingresos de 7 días en barras (el dato es discreto) con el Bs real de cada pago (depende de O02).

### Lote 3 — Sistema de componentes y listas
Plantilla base con tokens, componentes (tabla responsiva, tarjeta, insignia de estado, modal de confirmación, toast, formulario con validación en línea), CSS compilado una sola vez con el CLI independiente de Tailwind (binario, sin Node en producción) y versionado como `static/panel.css`, o CSS escrito a mano sobre los tokens: **decisión 1**. Se eliminan el CDN de Tailwind y los estilos en línea.
Listas rediseñadas: clientes (**incluye a quienes aún no tienen compras**, hoy ocultos), suscripciones (filtro por todos los estados, incluso los heredados), perfiles (el cambio de estado deja de enviarse solo al elegir; pide confirmación y avisa si hay una suscripción ligada), cuentas, plataformas. Exportación CSV donde tenga sentido; acciones en lote con confirmación.

### Lote 4 — Ficha de cliente 360 (depende de O02)
Resumen de lo que debe, historial de pagos y periodos, servicios con su estado real, acciones coherentes **iguales** en todas las pantallas (hoy la papelera aparece en una lista y no en otra), «Renovar todo» que genera una orden, anomalías del cliente, credenciales con «Revelar» (auditado, O01), registro de actividad.

### Lote 5 — Ajustes y salud real
Pantalla de salud que **prueba de verdad** Supabase, Redis y el webhook del bot y muestra latencia y última ejecución de cada job del scheduler; antigüedad de la tasa de cambio con alerta si supera 24 h y historial de tasas; páginas 404 y 500 con salida; validación de parámetros con mensaje.

### Lote 6 — Partir `router.py` y cierre
`admin_panel/router.py` (cerca de 2.000 líneas) se divide por dominio (dashboard, clientes, suscripciones, pagos, catálogo, ajustes, API). Pasada de accesibilidad y de móvil con las capturas del informe como prueba de regresión.

## NO incluye
Cambios de lógica de pagos o de estados (O02) · autenticación (O01) · PWA o API para clientes (O04) · rediseño de los mensajes del bot · reescritura del panel en un framework SPA.

## Retrocompatibilidad y despliegue
Mismas URLs del panel; sin cambio de datos salvo índices y la función de totales (se crean antes de desplegar y son aditivos). Cada lote es desplegable por separado y reversible; el Lote 1 se prueba con las mismas mediciones de la línea base.

## Casos de falla y salidas
Un widget que falla no tumba la página (muestra error y «Reintentar») · la caché caída degrada a consulta directa con aviso en salud · la función de totales ausente cae a la consulta anterior · petición lenta a los 3 a 5 s muestra «Está tardando más de lo normal» con Cancelar/Reintentar · el envío de un formulario nunca borra lo escrito si falla.

## Criterios de aceptación
- Dado el dashboard en frío, cuando carga, entonces la estructura aparece en < 600 ms y los datos en < 1,5 s (medido con el mismo método de la línea base).
- Dado un teléfono de 390 px, cuando se abre cualquier lista, entonces la acción principal de cada fila está visible sin desplazamiento lateral.
- Dado un botón con solo ícono, entonces tiene etiqueta accesible; el contraste cumple AA en todas las pantallas.
- Dado un fallo forzado de Supabase, entonces el panel muestra un error con acción, no una lista vacía.
- Dado cualquier GET del panel, entonces no hay escrituras (verificado en el código y por prueba).
- Dado un cliente sin compras registrado, entonces aparece en Clientes.

## Pruebas
Medición de tiempos antes/después con el mismo guion; pruebas de plantillas con datos hostiles y vacíos; pruebas de paginación y búsqueda; capturas de regresión en 1440 y 390 px; las que dependan de producción las ejecuta el Orquestador y el cierre lo declara.

## Decisiones que el Orquestador debe tomar
1. **CSS:** binario de Tailwind con archivo compilado versionado, o CSS propio sobre tokens (sin herramientas de compilación).
2. **Tono visual** (se propone en el Lote 0 con dos alternativas).
3. Qué hacer con «Cuentas por vencer» (mantener fecha de cobro real o eliminar).
4. Si se instala la skill `impeccable` para el Lote 3.

## Archivos esperados
`admin_panel/` (router dividido, `templates/` con base y parciales, `static/`) · `database/analytics.py` · `database/__init__.py` (acceso a datos) · `main.py` (middlewares) · `supabase_schema.sql` · `docs/DISENO.md` (nuevo) · `docs/ESTADO.md`.

## Plan por lotes
| Lote | Riesgo | Requiere |
|---|---|---|
| 0 tokens y dirección | 🟢 | aprobación visual |
| 1 infraestructura | 🔴 (migración) | Orquestador ejecuta SQL |
| 2 dashboard | 🟡 | Lote 1 |
| 3 componentes y listas | 🟢/🟡 | Lote 0, decisión 1 |
| 4 ficha 360 | 🟡 | O02 |
| 5 salud | 🟡 | — |
| 6 router y cierre | 🟢 | — |
