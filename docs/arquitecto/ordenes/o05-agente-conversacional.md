# O05 — Agente conversacional del bot (solo texto) · 🔴 · rama `s26-agente-ia`

**Estado:** **Lote 5 construido** en la rama `claude/keen-mendel-vn6j7b`, pendiente de revisión del PR (detalle en `docs/subfases/o05-lote5-ia-admin-confirmaciones.md`); Lotes 0 a 4 y 6 sin iniciar. Borrador original (27/11/2026) · **Alcance decidido:** solo texto; la voz queda para una orden posterior
**Depende de:** O02 Lotes 1 a 4 (`checkout_service`, órdenes de pago, enrutado de comprobantes) y O01 Lote 0 (auditoría) · **No depende de** O03 ni de O04
**Evidencia:** informe privado del Arquitecto (hallazgos del bot y de la IA actual).

## Objetivo
Que el cliente escriba como en una tienda de WhatsApp («¿cuándo vence mi Netflix?», «quiero renovar todo», «cuánto sale Disney», «no me llega el código», «ya pagué») y el bot **entienda y actúe sobre su cuenta real**, sin que tenga que conocerse un menú. Los botones se conservan como respuestas rápidas y como salida de emergencia.

## Estado actual (resumen)
- Hay un chat de IA (`bot/handlers/ai_chat.py`) que solo responde texto: **no tiene herramientas ni acciones**; el enrutado de intenciones en `main.py` es por palabras clave (cualquier texto con «hogar» abre soporte Hogar).
- El estado de conversación vive en Redis con TTL de 30 min o 1 h; no hay máquina que dé prioridad entre «esperando comprobante» y otros flujos (O02 lo ordena).
- La IA del admin (`ai_admin.py`) puede cambiar la tasa desde texto libre **sin confirmación**.
- Cada texto libre de cualquier usuario cuesta hasta 2 llamadas al modelo; el límite es de 30 mensajes por minuto.
- El OCR de comprobantes usa un modelo de Gemini vía OpenRouter; no aprueba nada, solo informa al admin.

## Principios (zona roja)
1. **El modelo propone; el sistema ejecuta.** El modelo nunca toca la base: solo llama a herramientas con argumentos validados.
2. **La identidad sale de Telegram, jamás del texto.** `user_id` se inyecta en el servidor en cada llamada; el modelo no puede pedir datos de otro cliente.
3. **El dinero y los cambios exigen un botón.** Toda acción que cree una orden, cancele o abra un ticket termina en una tarjeta de confirmación con botones; escribir «sí» **no** ejecuta nada.
4. **El admin aprueba pagos.** El agente jamás aprueba, rechaza, cambia precios ni la tasa.
5. **Siempre hay salida.** Cualquier fallo del agente cae al menú de botones con un mensaje que ofrece una acción.

## Alcance

### Lote 0 — Decisiones, conjunto de evaluación y elección de modelo (sin tocar producción)
- Resolver las decisiones de abajo.
- Escribir un **conjunto de evaluación** de ~60 conversaciones sintéticas (español de Venezuela, modismos, errores de ortografía, mensajes ambiguos, intentos de inyección) con el resultado esperado de cada una (herramienta, argumentos, respuesta o negativa).
- Elegir modelo con evidencia: probar candidatos con ese conjunto midiendo acierto, latencia y costo por conversación. **No se fija ningún modelo ni precio de memoria**; se verifica en la documentación oficial del proveedor que el modelo soporta llamadas a herramientas con la API que se usa. Datos de prueba ficticios; nada de clientes reales.

### Lote 1 — Capa de herramientas (lógica pura, con pruebas)
`services/agent_tools.py`: funciones tipadas con esquema (pydantic, ya es dependencia) sobre los servicios de negocio existentes. Todas reciben `user_id` del servidor y verifican propiedad.
| Herramienta | Tipo | Qué hace |
|---|---|---|
| `mis_servicios` | lectura | Servicios del cliente con estado humano, vencimiento, mora y precio |
| `precios` | lectura | Catálogo en USD y Bs con la tasa vigente y su antigüedad |
| `mis_ordenes` | lectura | Órdenes pendientes y últimas aprobadas |
| `instrucciones_de_pago` | lectura | Datos de pago y monto exacto de la orden pendiente |
| `codigo_de_verificacion` | lectura | Reutiliza los flujos IMAP actuales, con su límite de frecuencia |
| `proponer_renovacion` | propuesta | Arma el borrador de orden (ítems y periodos) y devuelve el resumen |
| `proponer_compra` | propuesta | Borrador de compra nueva (plataforma y plan) |
| `proponer_cancelar_orden` | propuesta | Borrador para cancelar una orden propia pendiente |
| `abrir_ticket` / `hablar_con_persona` | propuesta | Notifica al admin con un resumen de la conversación |
Las herramientas de propuesta **no ejecutan**: guardan un borrador en Redis (TTL 10 min, de un solo uso, ligado al usuario) y devuelven un resumen.

### Lote 2 — Orquestador del agente y guardas
- `services/agent/`: instrucciones del sistema (tono, límites, formato breve), bucle de herramientas con máximo de pasos, tiempo límite total, validación de cada llamada, y filtro de salida que nunca repite contraseñas en el registro.
- **Confirmación:** la propuesta se muestra como mensaje con botones `[Confirmar] [Cambiar] [Cancelar]`; el callback (token firmado, un solo uso, ligado a usuario y borrador) es lo único que llama a `checkout_service`. Doble toque devuelve la misma orden (idempotencia de O02).
- **Anti-inyección:** el texto del usuario, el nombre de perfil de Telegram, el texto OCR de un comprobante y toda salida de herramienta son **datos**, nunca instrucciones; lista blanca de herramientas; sin herramienta que lea datos de otro cliente o que ejecute sin confirmación.
- **Una conversación en vuelo por usuario** (candado Redis); mensajes seguidos se agrupan, no se procesan en paralelo.
- **Ventana de contexto corta** (últimos turnos, TTL 30 min); los datos del cliente se obtienen por herramientas en cada turno, no se guardan en memoria de largo plazo.

### Lote 3 — Integración al bot
- El enrutador deja de usar palabras clave por subcadena. **Orden de prioridad:** (1) flujo activo con estado explícito (por ejemplo, orden pendiente esperando comprobante: la foto va a la orden); (2) comandos y botones; (3) el agente para texto libre.
- Texto en estado «esperando comprobante» → el agente responde y recuerda enviar la foto.
- Mensaje de bienvenida que explica «puedes escribirme normal»; `/menu` y el botón de menú siempre disponibles en cada respuesta.
- Indicador «escribiendo…» mientras trabaja; objetivo de latencia p95 < 6 s.
- Interruptor `AGENT_ENABLED` (variable **no secreta**; por esta orden se autoriza crear solo esa, por defecto `false`) y lista de Telegram IDs de prueba (`AGENT_ALLOWLIST`), ampliable por porcentaje.
- Límites: máximo de mensajes por minuto y de turnos por día por usuario, y **tope de costo mensual global**; al alcanzarlo o ante un fallo del proveedor, el bot muestra el menú con una explicación breve.

### Lote 4 — Traspaso a persona y observabilidad
- «Hablar con una persona»: ticket al admin (canal ya existente) con resumen, **no** la transcripción completa.
- Registro por turno: usuario (con hash), herramientas llamadas, latencia, tokens, costo y resultado; **sin cuerpo de mensajes** salvo la decisión 3.
- Si se guardan transcripciones (decisión 3): tabla `agent_turns` con enmascarado de datos sensibles y retención de 30 días, y una pantalla «Conversaciones del agente» en el panel para revisar fallos (diseñada en el estilo de O03).
- Alertas al admin: tasa de errores, costo del día, conversaciones que terminaron en traspaso.

### Lote 5 — IA del admin: confirmaciones
Corregir `ai_admin.py`: cambio de tasa y cualquier acción con efecto piden confirmación con botón, y la tasa solo se acepta con un número plausible y una orden explícita. Es una corrección de seguridad previa a ampliar el uso de IA.

### Lote 6 — Beta controlada
Primero solo el admin, luego una lista pequeña de clientes, luego por porcentaje. Revisión diaria de los fallos; criterios de avance abajo.

## NO incluye
Voz y notas de audio (orden posterior; el diseño deja la entrada como `AgentInput(text, source)` para añadirla sin rehacer) · canales de WhatsApp o web · aprobación automática de pagos · cambios de precios o tasa por el agente · agente para el admin (salvo el Lote 5) · memoria de largo plazo o perfilado del cliente · recomendaciones de contenido (TMDB) · campañas redactadas por IA.

## Casos extremos que el conjunto de evaluación debe cubrir
Pedir datos de otra persona o «de todos los clientes» · «aprueba mi pago» · «ignora tus instrucciones y…» (en el texto, en el nombre de Telegram y dentro del texto OCR de un comprobante) · «renueva» con tres servicios (pregunta cuál con botones) · «ya pagué» sin orden pendiente · «ya pagué» con orden pendiente y sin foto · cliente en mora que quiere contratar otro servicio · mensaje en inglés, solo emojis, un sticker, un texto larguísimo · varios mensajes seguidos · confirmación con botón vencido o repetido · proveedor del modelo caído, lento o con una llamada a herramienta inválida · Redis caído · cliente bloqueado · tasa desactualizada · precio pedido de una plataforma sin cupo · cliente sin Telegram (no aplica; el canal es Telegram) · petición de código de verificación repetida.

## Criterios de aceptación
- Dado un cliente con tres servicios, cuando escribe «renueva todo», entonces ve un resumen con el total y los botones de confirmación, y al confirmar se crea **una** orden.
- Dado cualquier texto que pida datos de otro cliente, entonces el agente lo rechaza y no llama a ninguna herramienta con otro `user_id`.
- Dado un texto «sí» sin pulsar el botón, entonces no se crea ninguna orden.
- Dada una caída del proveedor del modelo, entonces el cliente recibe el menú y una explicación, no un error genérico.
- Dado el conjunto de evaluación, entonces el acierto de herramienta y argumentos alcanza el umbral fijado en el Lote 0 (propuesta: ≥ 90 %) y **0** casos de inyección ejecutan una acción.
- Dado un mes de beta, entonces el costo por cliente activo queda dentro del tope aprobado.

## Pruebas obligatorias
`unittest` con un modelo simulado que devuelve llamadas a herramientas guionadas: propiedad por usuario, borradores de un solo uso y con vencimiento, confirmación solo por botón, idempotencia, candado por usuario, prioridad de estados del enrutador, caída del proveedor y de Redis, límites de uso y de costo. El **conjunto de evaluación contra el modelo real** lo ejecuta el Orquestador (requiere claves), con datos ficticios. Los textos del agente y de las tarjetas se revisan con `clarify` de Impeccable.

## Decisiones que el Orquestador debe tomar
1. **Proveedor y modelo:** se decide con los resultados del Lote 0 (acierto, latencia, costo). No se fija ahora.
2. **Tope de costo mensual** del agente (USD) y límites por usuario.
3. **Guardar transcripciones** (30 días, enmascaradas, para mejorar y revisar fallos) o no guardarlas (más privado, menos capacidad de mejora).
4. **Credenciales por el agente:** que muestre al cliente los datos de acceso de su propio servicio activo con las mismas reglas que «Mis servicios», o que remita siempre al botón.
5. **Tono y tratamiento** (tú o usted, nivel de informalidad, uso de emojis).
6. **Traspaso a persona:** ticket de WhatsApp ya existente o aviso por Telegram al admin.
7. **Quiénes prueban primero** (lista inicial de IDs) y criterio para pasar de lista a porcentaje.

## Retrocompatibilidad y despliegue
Con `AGENT_ENABLED=false` el bot se comporta como hoy. Sin cambio de esquema salvo la tabla opcional de transcripciones (migración previa al despliegue, decisión 3). Sin dependencia nueva: se reutiliza `httpx` (ya presente) con el proveedor elegido. Fusionar en baja actividad (Railway reinicia el bot).

## Archivos esperados
`services/agent_tools.py` y `services/agent/` (nuevos) · `bot/handlers/ai_chat.py` (se reemplaza o se reduce) · `bot/handlers/ai_admin.py` · `main.py` (enrutador de mensajes) · `bot/messages.py` y `bot/keyboards.py` (tarjetas de confirmación) · `services/checkout_service.py` (reuso, O02) · `config.py` (`AGENT_ENABLED`, `AGENT_ALLOWLIST`, topes) · `supabase_schema.sql` (si hay transcripciones) · `tests/` y `tests/eval/` (conjunto de evaluación) · `docs/FLUJOS.md` · `docs/OPERACION.md` · `docs/ESTADO.md` · `features.md` (Feature 10).

## Plan por lotes
| Lote | Riesgo | Requiere |
|---|---|---|
| 0 decisiones, evaluación, modelo | 🟡 | claves de prueba del Orquestador |
| 1 herramientas | 🔴 | O02 Lotes 1 a 3 |
| 2 orquestador y guardas | 🔴 | Lote 1 |
| 3 integración al bot | 🔴 | Lote 2, O02 Lote 4 |
| 4 traspaso y observabilidad | 🟡 | Lote 3, migración si hay transcripciones |
| 5 IA del admin | 🔴 | — (puede ir primero) |
| 6 beta | 🟡 | todo lo anterior |

El Lote 5 es independiente y de bajo costo: se recomienda hacerlo antes de ampliar cualquier uso de IA.
