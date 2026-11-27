# O04 — API de clientes y PWA SmartFlixVE · 🔴 · rama `s25-pwa-cliente`

**Estado:** BORRADOR para veredicto del Orquestador (27/11/2026) · **Depende de:** O01 (seguridad), O02 (órdenes de pago), O03 (tokens de diseño) · **Implementa:** Fase 7 (7A, 7B, 7C parcial) de `features.md`
Estudio previo de factibilidad (02/11/2026) quedó fuera del repo (R00-6): se rehace brevemente en el Lote 0.

## Objetivo
Un espejo del servicio en una PWA instalable donde el cliente vea y gestione sus servicios, pague y renueve en una sola operación, contrate nuevos y siga sus pedidos, sin conocer rutas del bot. El bot de Telegram **sigue funcionando** y comparte la misma lógica; el admin aprueba los pagos igual que hoy.

## Principios
1. **Una sola lógica de negocio.** La PWA, el bot y el panel llaman a los mismos servicios (`checkout_service`, catálogo, suscripciones). Ninguna regla se duplica en el cliente.
2. **El servidor manda.** Precio, total, tasa, estado y propiedad se calculan y verifican en backend; el navegador nunca los decide.
3. **Aislamiento por cliente.** `user_id` sale de la sesión, jamás de la URL ni del cuerpo; cada acceso por ID verifica pertenencia.
4. **Aprobar pagos sigue siendo del admin.**

## Alcance

### Lote 0 — Decisiones y estudio
Resolver las decisiones de abajo; prototipo visual de 4 pantallas (inicio/catálogo, mis servicios, renovar, estado del pedido) en móvil, dirección «tienda de streaming» (referencias del estudio del 02/11/2026) con los tokens de O03.

### Lote 1 — Cimientos de la API (`/api/v1`)
- Rutas JSON versionadas en el mismo servicio FastAPI, validación con pydantic (ya es dependencia), formato de error uniforme con catálogo (código → mensaje humano → acción), paginación estándar, llaves de idempotencia en la creación de órdenes, tiempo de espera por endpoint.
- Autenticación de clientes: sesión opaca revocable en cookie `HttpOnly; Secure; SameSite=Lax` + CSRF; límite de intentos por IP y por cuenta. **No** se reutiliza la sesión del panel.
- Ingreso del cliente (decisión 1): enlace profundo `t.me/<bot>?start=login_<token>` o código de un solo uso enviado por el propio bot; el token caduca en minutos y es de un solo uso.
- CORS restringido a orígenes propios; documentación OpenAPI solo en desarrollo.

### Lote 2 — Lectura
`GET` del catálogo con precios en USD y Bs (tasa vigente y su antigüedad), perfil, mis servicios (estado humano, vencimiento, mora), historial de pagos y pedidos. **Credenciales:** solo bajo acción explícita («Revelar»), con registro y límite de frecuencia; nunca en el HTML inicial ni en caché del navegador (`Cache-Control: no-store`, fuera de la caché del *service worker*).

### Lote 3 — Escritura (usa O02)
Crear orden de renovación y/o de compra nueva con N ítems (el servidor arma el manifiesto y los totales), adjuntar comprobante (imagen o PDF; validación de tipo real y tamaño, renombrado, análisis OCR como señal; **decisión 2** sobre dónde se guarda), cancelar una orden propia pendiente, seguimiento de estado. Notificación por Telegram al aprobar o rechazar (canal existente).

### Lote 4 — PWA (cascarón y diseño)
Manifiesto, íconos, *service worker* que solo cachea el cascarón y recursos estáticos, instalable, pantalla sin conexión con explicación y reintento. Frontera de errores global y por ruta, pantalla de «hay una versión nueva» sin perder lo escrito, tiempos de espera y reintentos con espera creciente. Stack según decisión 3.

### Lote 5 — Pantallas
Inicio y catálogo · Mis servicios (tarjetas con estado y botón principal) · Renovar (selección múltiple, periodos, total en vivo, un solo comprobante) · Contratar · Estado del pedido (línea de tiempo) · Ayuda (soporte actual vía bot/WhatsApp con el ticket ya existente). Movimiento y estados vacíos con carácter en las pantallas de venta.

### Lote 6 — Lanzamiento controlado
Beta con un grupo pequeño de clientes (bandera `PWA_ENABLED` no secreta), medición (Web Vitals, errores), ruta de vuelta al bot en toda pantalla, comunicación a clientes. Después: página de aterrizaje 7A y contenido destacado 7C (órdenes aparte).

## Fuera de alcance (órdenes futuras)
Notificaciones push web (requiere biblioteca nueva y claves VAPID; en iOS solo con la PWA instalada) · referidos, puntos, certificados · pasarela con verificación bancaria automática · apps nativas · **agente conversacional por texto y voz** (propuesto como O05, se apoya en esta API y en `checkout_service`).

## Seguridad (zona roja)
Todo el Piso de Seguridad de ATLAS: sin secretos en el frontend ni variables públicas con valores sensibles; escaneo del paquete compilado en cada despliegue; CSP estricta; rate limiting en ingreso, creación de órdenes y subida de archivos; datos personales fuera de logs; verificación anti-IDOR en cada endpoint con prueba; cabeceras como en O01; sin `localStorage` para sesión ni credenciales.

## Casos de falla y salidas
Cliente sin Telegram (hoy existen) → ruta de acceso alterna según decisión 1 · enlace de ingreso vencido → reenvío con límite · sesión vencida en medio de un pago → vuelve donde estaba, conserva el comprobante local · subida de comprobante interrumpida → reintento reanudable, la orden queda pendiente · doble toque en «Pagar» → la llave de idempotencia devuelve la misma orden · tasa cambia mientras elige → rige la congelada al crear la orden, con aviso previo del cambio · sin conexión → cascarón y mensaje claro, sin perder la selección.

## Criterios de aceptación
- Dado un cliente con tres servicios, cuando renueva los tres desde la PWA con un comprobante, entonces el admin ve **una** orden con tres ítems y el total correcto.
- Dado otro cliente, cuando manipula un ID de orden o suscripción ajeno, entonces recibe 404 (no 403) y no se filtra información.
- Dado un cliente, cuando abre la PWA instalada sin conexión, entonces ve el cascarón y el estado «sin conexión», no una pantalla en blanco.
- Dado el paquete de producción, entonces el escaneo no encuentra secretos.
- Dado un móvil de gama media en 4G, entonces el contenido principal aparece en < 2,5 s y el JS inicial pesa < 200 KB comprimido.
- Dado cualquier pantalla, entonces existe un camino de vuelta al bot o a ayuda.

## Pruebas obligatorias
Aislamiento por cliente (cada endpoint), idempotencia de órdenes, validación de archivos, expiración y un solo uso del enlace de ingreso, límites de frecuencia, resiliencia de interfaz (tiempo agotado, 500, sin red, fallo de render) en cada pantalla. Lo que requiera Telegram o Supabase reales lo prueba el Orquestador.

## Decisiones que el Orquestador debe tomar
1. **Cómo ingresa el cliente:** a través del bot (recomendado: costo cero, ya es su canal) y qué hacer con quienes no tienen Telegram (código entregado por el admin / WhatsApp).
2. **Almacén de comprobantes:** hoy solo se guarda el texto del OCR, no la imagen. Guardar la imagen implica un *bucket* privado de Supabase Storage (cambio de infraestructura) y política de retención.
3. **Stack del frontend:** **A** Vite + React o Preact compilado y servido por el mismo servicio FastAPI (más «app», requiere Node en el build de Railway y una herramienta nueva); **B** páginas renderizadas por el servidor con Jinja + Alpine (sin herramienta nueva, menos fluida). Se recomienda A solo si se aprueba el build; ambas dependencias son disparadores de aprobación.
4. ¿Mismo dominio (`/app`) o subdominio propio? (afecta cookies y CSP).
5. Alcance de la beta y fecha.
6. Skill de diseño: `impeccable` no está instalada; ver O03.

## Archivos esperados
`api/` (nuevo: `v1/` con routers, esquemas, errores) · `services/customer_auth.py` · `database/customer_sessions.py` · `services/checkout_service.py` (reuso) · `main.py` (montaje, middlewares) · `web/` (nuevo, fuente del frontend) · `static/pwa/` (salida compilada o plantillas) · `supabase_schema.sql` (sesiones de cliente, tokens de ingreso, almacén de comprobantes) · `tests/` · `docs/FLUJOS.md` · `docs/DISENO.md` · `docs/OPERACION.md` · `docs/ESTADO.md` · `features.md` (Fase 7).

## Plan por lotes
| Lote | Riesgo | Requiere |
|---|---|---|
| 0 decisiones y prototipo | 🟢 | veredicto |
| 1 cimientos de API y acceso | 🔴 | migración, O01 |
| 2 lectura | 🔴 | Lote 1 |
| 3 escritura | 🔴 | O02 completo |
| 4 cascarón PWA | 🟡 | decisión 3 |
| 5 pantallas | 🟢/🟡 | Lotes 2 a 4, O03 |
| 6 beta | 🟡 | todo lo anterior |
