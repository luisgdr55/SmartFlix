# SmartFlixVE Bot — Features Planificadas

> Documento de roadmap. Cada feature incluye descripción, impacto estimado,
> complejidad de implementación y archivos principales a modificar.

---

## Estado de features

| # | Feature | Categoría | Prioridad | Estado |
|---|---------|-----------|-----------|--------|
| 1 | Broadcast segmentado por plataforma | Retención/Adquisición | 🔴 Alta | 📋 Pendiente |
| 2 | Sistema de referidos | Adquisición | 🔴 Alta | 📋 Pendiente |
| 3 | Reporte semanal automático al admin | Automatización | 🔴 Alta | 📋 Pendiente |
| 4 | Prueba gratuita 24h | Adquisición | 🟡 Media | 📋 Pendiente |
| 5 | Recomendaciones personalizadas TMDB | Retención | 🟡 Media | 📋 Pendiente |
| 6 | Notificaciones de estrenos por plataforma | Retención | 🟡 Media | 📋 Pendiente |
| 7 | Sistema de puntos por lealtad | Retención | 🟡 Media | 📋 Pendiente |
| 8 | Recordatorio inteligente de actividad | Retención | 🟡 Media | 📋 Pendiente |
| 9 | Planes familiares/compartidos | Adquisición | 🟠 Media-Alta | 📋 Pendiente |
| 10 | Asistente de soporte con IA | Automatización | 🟡 Media | ⚠️ Parcial — códigos verificación IMAP y flujo hogar Gemini implementados (Sesión 15) |
| 11 | Encuesta de satisfacción post-activación | Retención | 🟢 Baja | 📋 Pendiente |
| 12 | Dashboard de métricas de negocio | Visibilidad admin | 🟢 Baja | 📋 Pendiente |
| 13 | Segmentación CRM para broadcasts | Visibilidad admin | 🟡 Media | 📋 Pendiente |

---

## Detalle de features

---

### FEATURE 1 — Broadcast segmentado por plataforma
**Categoría:** Retención / Adquisición  
**Prioridad:** 🔴 Alta  
**Complejidad:** Baja  

**Descripción:**  
Desde `/admin`, el admin redacta un mensaje, selecciona una o varias plataformas y el bot lo envía automáticamente a todos los suscriptores activos de esas plataformas. Ideal para anunciar estrenos, series o películas específicas de cada servicio.

**Ejemplo de uso:**  
*"Nueva temporada de Stranger Things disponible en Netflix 🎬 ¡Disfrútala con tu suscripción activa!"* → enviado solo a clientes con Netflix activo.

**Archivos a modificar:**
- `bot/handlers/admin.py` — nuevo comando `/broadcast` o botón en menú admin
- `bot/keyboards.py` — teclado de selección de plataformas para broadcast
- `database/subscriptions.py` — nueva función `get_active_users_by_platform(platform_id)`
- `services/notification_service.py` — función `send_broadcast(user_ids, message)`
- `scheduler/jobs.py` — opcional: broadcasts programados

**Cambios en BD:** Ninguno  

---

### FEATURE 2 — Sistema de referidos
**Categoría:** Adquisición  
**Prioridad:** 🔴 Alta  
**Complejidad:** Media  

**Descripción:**  
Cada cliente tiene un código único de referido generado automáticamente. Cuando un nuevo usuario se registra usando ese código, ambos reciben un beneficio configurable (días gratis, descuento en próxima renovación). El bot trackea las conversiones y notifica a ambas partes.

**Ejemplo de uso:**  
Cliente envía su código a un amigo → amigo se registra → ambos reciben 5 días gratis en su próxima renovación.

**Archivos a modificar:**
- `database/users.py` — añadir campo `referral_code` (único), `referred_by` (FK users)
- `bot/handlers/subscription.py` — detectar código en `/start` o comando `/referido`
- `bot/handlers/admin.py` — panel de referidos: quién refirió a quién, cuántos
- `services/notification_service.py` — notificación de recompensa a ambas partes

**Cambios en BD:**
```sql
ALTER TABLE users ADD COLUMN referral_code VARCHAR(10) UNIQUE;
ALTER TABLE users ADD COLUMN referred_by UUID REFERENCES users(id);
ALTER TABLE users ADD COLUMN referral_bonus_days INT DEFAULT 0;
CREATE TABLE referral_rewards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id UUID REFERENCES users(id),
    referred_id UUID REFERENCES users(id),
    status VARCHAR(20) DEFAULT 'pending', -- pending|applied
    bonus_days INT DEFAULT 5,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### FEATURE 3 — Reporte semanal automático al admin
**Categoría:** Automatización  
**Prioridad:** 🔴 Alta  
**Complejidad:** Baja  

**Descripción:**  
Cada lunes a las 9AM (hora Venezuela), el bot envía al admin un resumen por Telegram con: ingresos de la semana, clientes nuevos, renovaciones realizadas, clientes que vencen esa semana, plataforma más vendida y clientes con deuda activa.

**Ejemplo de mensaje:**
📊 Reporte semanal SmartFlixVE
Semana del 21 al 27 de abril
💰 Ingresos: $45.00
👥 Clientes nuevos: 3
🔄 Renovaciones: 7
⚠️ Vencen esta semana: 4
🏆 Plataforma top: Netflix (5 activos)
🔴 Con deuda: 2 clientes

**Archivos a modificar:**
- `scheduler/jobs.py` — nuevo `job_weekly_admin_report()`, CronTrigger lunes 9AM
- `services/notification_service.py` — función `send_weekly_report()`
- `database/analytics.py` — función `get_weekly_stats()`

**Cambios en BD:** Ninguno

---

### FEATURE 4 — Prueba gratuita 24h
**Categoría:** Adquisición  
**Prioridad:** 🟠 Media-Alta  
**Complejidad:** Media  

**Descripción:**  
Un usuario nuevo puede activar una cuenta express de 24h gratis, una sola vez. El sistema verifica que no haya usado el beneficio antes (flag en users). Al vencer, el bot le invita a suscribirse con un mensaje personalizado.

**Archivos a modificar:**
- `database/users.py` — campo `free_trial_used BOOLEAN DEFAULT FALSE`
- `bot/handlers/subscription.py` — detectar elegibilidad, activar sin pago
- `bot/handlers/admin.py` — notificación al admin de nueva prueba activada
- `scheduler/jobs.py` — `job_express_release` ya maneja el vencimiento

**Cambios en BD:**
```sql
ALTER TABLE users ADD COLUMN free_trial_used BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN free_trial_at TIMESTAMPTZ;
```

---

### FEATURE 5 — Recomendaciones personalizadas TMDB
**Categoría:** Retención  
**Prioridad:** 🟡 Media  
**Complejidad:** Media  

**Descripción:**  
Usando TMDB API (ya integrada) y el historial de plataformas de cada cliente, el bot envía semanalmente recomendaciones de contenido nuevo disponible en las plataformas suscritas. El scheduler filtra por plataforma y envía mensajes personalizados.

**Ejemplo de uso:**  
*"🎬 Esta semana en tu Netflix: [título] ⭐ 8.4 — Drama | Temporada 2 disponible"*

**Archivos a modificar:**
- `scheduler/jobs.py` — nuevo `job_content_recommendations()`, CronTrigger viernes 6PM
- `services/notification_service.py` — `send_content_recommendation(user, content)`
- nueva `services/tmdb_service.py` — wrapper TMDB: trending by provider, new releases

**Cambios en BD:** Ninguno

---

### FEATURE 6 — Notificaciones de estrenos por plataforma
**Categoría:** Retención  
**Prioridad:** 🟡 Media  
**Complejidad:** Baja  

**Descripción:**  
El admin puede enviar un anuncio de estreno específico a todos los suscriptores de una plataforma. Diferente al broadcast general — aquí el admin ingresa el título, el bot busca la info en TMDB (poster, sinopsis, rating) y arma un mensaje visual atractivo automáticamente.

**Archivos a modificar:**
- `bot/handlers/admin.py` — flujo: admin ingresa título → bot busca en TMDB → preview → confirmar envío
- `services/tmdb_service.py` — búsqueda por título
- `services/notification_service.py` — envío con imagen (sendPhoto)

**Cambios en BD:** Ninguno

---

### FEATURE 7 — Sistema de puntos por lealtad
**Categoría:** Retención  
**Prioridad:** 🟡 Media  
**Complejidad:** Media-Alta  

**Descripción:**  
Cada renovación puntual (antes del vencimiento) suma puntos al cliente. Al acumular un umbral configurable, recibe días gratis automáticamente. El bot notifica el saldo de puntos y cuando se alcanza el premio.

**Ejemplo:** 5 renovaciones puntuales = 7 días gratis en la siguiente.

**Archivos a modificar:**
- `database/users.py` — campo `loyalty_points INT DEFAULT 0`
- `bot/handlers/admin.py` — sumar puntos al aprobar renovación puntual
- `services/notification_service.py` — notificación de puntos acumulados y premio

**Cambios en BD:**
```sql
ALTER TABLE users ADD COLUMN loyalty_points INT DEFAULT 0;
ALTER TABLE users ADD COLUMN loyalty_total_earned INT DEFAULT 0;
CREATE TABLE loyalty_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    points INT,
    reason VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

### FEATURE 8 — Recordatorio inteligente de actividad
**Categoría:** Retención  
**Prioridad:** 🟡 Media  
**Complejidad:** Baja  

**Descripción:**  
Si un cliente lleva 5+ días con suscripción activa sin interactuar con el bot ni pedir soporte, recibe un mensaje amigable verificando que todo esté bien. Reduce el churn silencioso y abre canal de comunicación proactivo.

**Archivos a modificar:**
- `scheduler/jobs.py` — nuevo `job_engagement_check()`, CronTrigger cada 5 días
- `database/users.py` — usar campo `last_seen` existente
- `services/notification_service.py` — mensaje de check-in

**Cambios en BD:** Ninguno (usa `last_seen` existente en users)

---

### FEATURE 9 — Planes familiares / compartidos
**Categoría:** Adquisición  
**Prioridad:** 🟠 Media-Alta  
**Complejidad:** Alta  

**Descripción:**  
Un cliente puede contratar 2 o 3 perfiles de la misma plataforma con descuento por volumen. El bot gestiona cada perfil individualmente pero los agrupa en un solo pago. Aumenta el ticket promedio y atrae grupos familiares.

**Archivos a modificar:**
- `database/subscriptions.py` — campo `group_id` para agrupar subs del mismo pago
- `bot/handlers/subscription.py` — flujo de selección de cantidad de perfiles
- `bot/handlers/admin.py` — aprobación grupal: un pago activa N perfiles
- `database/platforms.py` — precios por volumen (2x, 3x con descuento)

**Cambios en BD:**
```sql
ALTER TABLE subscriptions ADD COLUMN group_id UUID;
ALTER TABLE platforms ADD COLUMN price_2x_usd FLOAT;
ALTER TABLE platforms ADD COLUMN price_3x_usd FLOAT;
```

---

### FEATURE 10 — Asistente de soporte con IA
**Categoría:** Automatización  
**Prioridad:** 🟡 Media  
**Complejidad:** Media  

**Descripción:**  
Cuando un cliente reporta un problema al bot, Gemini (ya integrado) analiza el mensaje y responde automáticamente con pasos de solución según la plataforma. Solo escala al admin si el cliente confirma que no se resolvió. Reduce tickets manuales.

**Archivos a modificar:**
- `bot/handlers/support.py` — integrar Gemini con contexto de la suscripción del cliente
- `services/gemini_service.py` — prompt especializado en troubleshooting de streaming
- `services/notification_service.py` — escalación al admin si no se resuelve

**Cambios en BD:** Ninguno

---

### FEATURE 11 — Encuesta de satisfacción post-activación
**Categoría:** Retención  
**Prioridad:** 🟢 Baja  
**Complejidad:** Baja  

**Descripción:**  
48 horas después de activar una suscripción, el bot pregunta al cliente su experiencia con 3 botones (😊 Excelente / 😐 Regular / 😞 Mal). Si responde negativo, notifica al admin para intervenir. Permite detectar problemas antes de que el cliente cancele.

**Archivos a modificar:**
- `scheduler/jobs.py` — nuevo `job_satisfaction_survey()`, IntervalTrigger cada hora
- `database/subscriptions.py` — campo `survey_sent BOOLEAN DEFAULT FALSE`
- `bot/handlers/subscription.py` — handler para respuestas de encuesta
- `services/notification_service.py` — envío de encuesta y alerta al admin

**Cambios en BD:**
```sql
ALTER TABLE subscriptions ADD COLUMN survey_sent BOOLEAN DEFAULT FALSE;
ALTER TABLE subscriptions ADD COLUMN survey_response VARCHAR(10); -- positive|neutral|negative
ALTER TABLE subscriptions ADD COLUMN survey_sent_at TIMESTAMPTZ;
```

---

### FEATURE 12 — Dashboard de métricas de negocio
**Categoría:** Visibilidad admin  
**Prioridad:** 🟢 Baja  
**Complejidad:** Media  

**Descripción:**  
Nueva sección en el panel web con métricas avanzadas: tasa de renovación por plataforma, clientes en riesgo de churn, ingreso proyectado del mes, LTV promedio por cliente, y comparativa semana a semana.

**Archivos a modificar:**
- `admin_panel/router.py` — nuevo endpoint `/panel/metrics`
- `admin_panel/templates/` — nueva página `metrics.html`
- `database/analytics.py` — funciones de métricas avanzadas

**Cambios en BD:** Ninguno (todo calculado sobre datos existentes)

---

### FEATURE 13 — Segmentación CRM para broadcasts
**Categoría:** Visibilidad admin  
**Prioridad:** 🟡 Media  
**Complejidad:** Media  

**Descripción:**  
Desde el panel web, el admin puede enviar mensajes segmentados a: todos los clientes, suscriptores de una plataforma específica, clientes que vencen esta semana, clientes inactivos hace 30+ días, o clientes con deuda. Un CRM básico integrado.

**Archivos a modificar:**
- `admin_panel/router.py` — endpoints de broadcast con filtros
- `admin_panel/templates/` — UI de composición y segmentación
- `database/subscriptions.py` — queries de segmentación
- `services/notification_service.py` — envío masivo con rate limiting

**Cambios en BD:** Ninguno

---

## Fases de implementación prioritaria

### FASE 1 — Correcciones urgentes
| Sub-fase | Descripción | Archivos | Estado |
|----------|-------------|----------|--------|
| 1A | Reporte diario: clientes de últimos 7 días en vez de hoy | `analytics.py` | ✅ Implementado |
| 1B | Reporte de ganancia neta real del mes | `jobs.py` | ✅ Implementado |

### FASE 2 — Afiliación manual mejorada vía Telegram
| Sub-fase | Descripción | Archivos | Estado |
|----------|-------------|----------|--------|
| 2A | /afiliar pregunta si es cliente nuevo o existente + lista de clientes | `afiliar.py` | ✅ Implementado |
| 2B | Selección manual de perfil disponible en vez de asignación automática | `afiliar.py`, `profiles.py` | ✅ Implementado |
| 2C | Ticket detallado copiable al finalizar afiliación | `afiliar.py` | ✅ Implementado |

### FASE 3 — Renovación manual vía Telegram
| Sub-fase | Descripción | Archivos | Estado |
|----------|-------------|----------|--------|
| 3A | Comando /renovar para admin — selecciona cliente y servicio a renovar | `renovar.py`, `subscriptions.py` | ✅ Implementado |
| 3B | Compatible con clientes sin telegram_id (afiliados manualmente) | `renovar.py` | ✅ Implementado |

### FASE 4 — Afiliación y renovación vía Dashboard web
| Sub-fase | Descripción | Archivos | Estado |
|----------|-------------|----------|--------|
| 4A | Botón "Agregar suscripción" en ficha de cliente del panel web | `router.py`, templates | 📋 Pendiente |
| 4B | Botón "Renovar" por suscripción en ficha de cliente del panel web | `router.py`, templates | 📋 Pendiente |

### FASE 5 — Migración de cuenta Netflix
| Sub-fase | Descripción | Archivos | Estado |
|----------|-------------|----------|--------|
| 5A | Comando /migrar para admin — cambia account_id del perfil asignado | `admin.py`, `profiles.py` | 📋 Pendiente |
| 5B | Notificación automática al cliente con nuevas credenciales | `notification_service.py` | 📋 Pendiente |

---

### FASE 6 — Módulo de Soporte y Tickets (Netflix + General)

#### 6A — Sistema de tickets de soporte vía Telegram
**Descripción:** Cliente reporta problema (bloqueo de hogar, credenciales, etc.)
El bot recopila evidencia y datos, genera ticket al admin.

**Flujo completo:**
1. Cliente escribe "bloqueo" / "problema" o pulsa botón de soporte
2. Bot pregunta tipo de problema
3. Si es bloqueo Netflix → ofrece las 3 opciones (ver 6B)
4. Si es otro problema → recopila descripción + captura de pantalla opcional
5. Ticket llega al admin con botones: [✅ En proceso] [🔧 Resolver] [❌ Rechazar]
6. Admin pulsa "En proceso" → cliente recibe: 
   "✅ Pago verificado. Tu solicitud está en proceso. 
   Te notificaremos en cuanto esté lista. ⏱️ Tiempo estimado: minutos u horas."
7. Admin resuelve → ingresa nuevas credenciales si aplica → 
   cliente recibe ticket completo automáticamente

**Cambios en BD:**
```sql
CREATE TABLE support_tickets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id),
    type VARCHAR(50), -- home_restriction|credential_issue|premium_member|migration|other
    status VARCHAR(20) DEFAULT 'open', -- open|in_progress|resolved|rejected
    evidence_url TEXT,
    details JSONB,
    admin_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);
```

**Archivos:**
- `bot/handlers/soporte.py` — nuevo módulo
- `database/tickets.py` — CRUD de tickets
- `admin_panel/router.py` — endpoints de gestión de tickets
- `admin_panel/templates/tickets.html` — vista de tickets en panel web
- `main.py` — registro de handlers

---

#### 6B — Planes Netflix diferenciados con flujo de bloqueo de hogar

**Los 3 planes Netflix:**

| Plan | Precio | Descripción |
|------|--------|-------------|
| Netflix Estándar | $5/mes | Perfil compartido. Incluye 1 migración de perfil gratis si hay bloqueo de hogar. A partir de la 2da migración: $1 adicional. Conserva historial. |
| Netflix Miembro Extra Premium | $7/mes | Perfil con TUS credenciales personales (email y PIN que tú eliges). Sin restricciones de hogar. Perfil 100% tuyo. |
| Netflix Miembro Extra Premium + Migración de historial | $8/mes | Todo lo del plan Premium + migración de tu historial actual al nuevo perfil. |

**Flujo cuando cliente reporta bloqueo (Estándar):**

Bot detecta bloqueo → muestra opciones:
1️⃣ MIGRACIÓN ESTÁNDAR GRATUITA (si es primera vez)
Tu perfil con TODO tu historial pasa a otra cuenta.
Sin costo adicional. ⏱️ Minutos u horas.
2️⃣ UPGRADE A MIEMBRO EXTRA PREMIUM — $7/mes
Tu propio perfil con tus credenciales.
Nunca más bloqueos de hogar.
📧 Email propio | 🔢 PIN que tú eliges
3️⃣ UPGRADE PREMIUM + HISTORIAL — $8/mes
Todo lo anterior + migramos tu historial actual.
Conservas todo lo que has visto.

**Si elige Premium o Premium+Historial:**
Bot solicita en pasos:
- 📧 Correo electrónico personal
- 📱 Número de teléfono
- 👤 Nombre de perfil deseado
- 🔢 PIN deseado (4 dígitos)
- 💳 Comprobante de pago

Admin recibe ticket completo → aprueba pago → cliente recibe:
*"✅ Pago verificado. Tu solicitud está en proceso..."*
→ Admin configura → cierra ticket → cliente recibe credenciales automáticamente.

**Archivos:**
- `bot/handlers/soporte.py` — flujo de bloqueo Netflix
- `database/tickets.py` — tipos de ticket Netflix
- `database/platforms.py` — nuevos planes Netflix (3 variantes)

---

### FASE 7 — PWA SmartFlixVE (Portal Web del Cliente)

#### 7A — Landing Page
**Descripción:** Página pública de alto impacto visual

**Secciones:**
- **Hero animado** — banner con efecto parallax mostrando logos de plataformas disponibles, tagline y CTA "Suscríbete ahora"
- **Precios en tiempo real** — cards por plataforma con precio calculado en Bs (tasa Binance live)
- **¿Por qué SmartFlixVE?** — sección FAQ explicando diferencias vs competencia barata: estabilidad, historial conservado, soporte real, nivel corporativo
- **Clientes fieles** — banner/carrusel animado con nombres o avatares de clientes más antiguos (con su permiso) o contador de clientes activos
- **Recomendaciones de contenido** — sección visual de películas/series recomendadas, administrable desde el dashboard
- **Planes Netflix explicados** — comparativa visual de los 3 planes con beneficios detallados
- **Testimonios** — sección de reseñas de clientes reales
- **CTA final** — formulario de contacto por WhatsApp o inicio de suscripción

**Stack:** React + Tailwind CSS, desplegado en Railway o Vercel

---

#### 7B — Portal del Cliente (autenticado)
**Descripción:** Dashboard personal del suscriptor

**Funciones:**
- Login por número de teléfono + código de verificación, o por email
- Ver suscripciones activas con fecha de vencimiento y estado
- Ver credenciales completas (email, perfil, PIN)
- Renovar suscripción y subir comprobante de pago
- Ver estado de tickets de soporte en tiempo real
- Reportar problema con botón directo
- Historial de pagos y suscripciones anteriores
- Sistema de referidos: código único + link copiable + QR
- Notificaciones push para vencimientos y respuestas de soporte

---

#### 7C — Sección de Recomendaciones (administrable)
**Descripción:** Curador de contenido administrado desde el dashboard

**Funciones para el admin:**
- Desde Panel Admin → nueva sección "Contenido Destacado"
- Admin busca título → TMDB trae poster, sinopsis, rating automáticamente
- Admin elige plataforma, añade nota personalizada ("¡Imperdible! ⭐")
- Publica en la PWA al instante
- Los clientes ven recomendaciones filtradas por sus plataformas suscritas

**Cambios en BD:**
```sql
CREATE TABLE featured_content (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_id UUID REFERENCES platforms(id),
    title VARCHAR(200),
    tmdb_id INT,
    poster_url TEXT,
    synopsis TEXT,
    rating FLOAT,
    admin_note TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

#### 7D — Ideas creativas adicionales para la PWA

| Idea | Descripción | Impacto |
|------|-------------|---------|
| **Contador en vivo** | "127 clientes disfrutando SmartFlixVE ahora mismo" — número real de suscripciones activas | Alto — genera FOMO |
| **Badge de cliente fiel** | Insignias para clientes según antigüedad: 🥉 3 meses, 🥈 6 meses, 🥇 1 año | Alto — gamificación y retención |
| **Calculadora de ahorro** | "Con SmartFlixVE ahorras $X vs contratar Netflix directamente" | Alto — justifica precio |
| **Comparativa vs competencia** | Tabla visual: SmartFlixVE vs cuentas baratas — estabilidad, soporte, historial | Alto — educa al cliente |
| **WhatsApp flotante** | Botón fijo de WhatsApp para soporte inmediato | Medio — accesibilidad |
| **Modo oscuro** | Toggle dark/light mode — estética gaming/streaming | Medio — UX |
| **Certificado de cliente** | PDF descargable personalizado con nombre del cliente y tiempo de membresía | Medio — fidelización emocional |
| **Referido con preview** | Cuando alguien abre un link de referido, ve quién lo invitó con foto/nombre | Alto — personalización |
| **Notificación de estreno** | Push notification cuando admin publica nuevo contenido recomendado | Alto — engagement |
| **Estado del servicio** | Página de status en tiempo real: "Todos los servicios operativos ✅" | Medio — confianza corporativa |

---

### FASE 8 — Pasarela de pagos unificada

**Categoría:** Infraestructura de pagos / Adquisición / Retención
**Prioridad:** 🔴 Alta
**Complejidad:** Alta
**Estado:** 📋 Diseñado — pendiente de implementar

---

#### Problema que resuelve

La pasarela actual solo modela dos operaciones aisladas: comprar nuevo (`cart:{tid}`)
y renovar lo mismo (`renewal_cart:{tid}`). Cada ítem es siempre 1 período, un pago
equivale a una suscripción, y no existe entidad de pago (los campos viven en
`subscriptions`). Esto deja tres flujos reales sin soporte:

1. **Mora multi-período.** Un cliente que debe varios meses (ej. Ray, cubierto hasta
   19/05, hoy debe 1 mes) no puede ponerse al día pagando N períodos de golpe ni ver
   cuánto debe y desde cuándo.
2. **Pago selectivo.** Un cliente con varios perfiles no puede elegir cuáles paga,
   cuántos meses de cada uno, y cuáles deja debiendo.
3. **Cambio de plataforma.** Cuando una suscripción vence y el cliente quiere otra
   plataforma en su lugar (ej. venció Disney, ahora quiere Netflix), no hay operación
   que libere el perfil viejo, asigne el nuevo y cobre el precio distinto como una
   sola transacción.

---

#### Decisiones de negocio (fijadas, sesión 17)

| # | Decisión | Implicación |
|---|----------|-------------|
| 1 | Deuda sostenida con servicio activo | El servicio NO se corta automáticamente por mora |
| 2 | Corte automático D+7 eliminado → corte manual por el admin | Conserva clientes fieles pero atrasados; reusa cancelación manual de sesión 9 |
| 3 | Recordatorios de deuda se suspenden tras D+6 | A partir de D+7 la deuda solo es visible en el panel |
| 4 | Facturación por aniversario en meses calendario | `relativedelta(months=N)`, NO `timedelta(days=30)` |
| 5 | Pago parcial cubre el período más antiguo primero | Ray paga 1 mes → cubre 19/05→19/06, sigue debiendo 19/06→19/07 |
| 6 | El período disfrutado se cobra (no se perdona) | Pagó por el tiempo que tuvo acceso |
| 7 | Cambio solo sobre suscripción YA vencida | No hay cambio a mitad de período |
| 8 | Cambio muta el mismo `subscription_id` | Historial continuo; arranque de cobertura fresco desde la aprobación |
| 9 | Cambio exige saldar deuda previa de la plataforma que se abandona | Disfrutó ese mes, lo paga antes de cambiar |
| 10 | Tickets en Bs con tasa Binance fijada manualmente | Ya implementado en Lote 0 (commits f672f43, c28112f) |

---

#### Arquitectura — tabla `checkout_orders` (un pago → N efectos)

Inspirada en el patrón order/order-items de pasarelas reales (Stripe PaymentIntent →
N efectos). El admin aprueba UNA orden, no N suscripciones. Deja la puerta lista para
referidos, lealtad y planes familiares (features 2, 7, 9) que también necesitan un
registro de pago real.

```sql
CREATE TABLE checkout_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(20) DEFAULT 'pending',   -- pending|approved|rejected|expired
    manifest JSONB NOT NULL,                -- array de ítems tipados (ver abajo)
    total_usd DECIMAL(10,2) NOT NULL,
    total_bs DECIMAL(10,2),
    rate_used DECIMAL(10,2),                -- tasa Binance congelada al abrir el checkout
    payment_reference VARCHAR(255),
    payment_image_url TEXT,                 -- texto OCR (igual que subscriptions hoy)
    payment_confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_checkout_orders_user_id ON checkout_orders(user_id);
CREATE INDEX idx_checkout_orders_status ON checkout_orders(status);
```

---

#### El manifest — ítem tipado (resuelve los 3 flujos con una estructura)

```jsonc
{
  "tipo": "renovacion" | "nueva" | "cambio",
  "subscription_id": "<uuid|null>",        // sub a mutar (renovacion/cambio)
  "platform_id": "<uuid>",                 // plataforma destino (la nueva en cambio)
  "platform_origen_id": "<uuid|null>",     // solo cambio: plataforma que se abandona
  "profile_id": "<uuid|null>",             // se asigna al aprobar
  "plan_type": "monthly",
  "periodos": 1,                           // ≥1 → resuelve la mora multi-período
  "cobertura_desde": "2026-05-19",
  "cobertura_hasta": "2026-06-19",         // base + relativedelta(months=periodos)
  "precio_unitario_usd": 5.0,
  "cargo_deuda_usd": 0.0,                   // solo cambio: deuda previa a saldar
  "subtotal_usd": 5.0
}
```

**Cómo cada tipo resuelve su flujo al aprobar la orden:**

- **`renovacion` con `periodos: N`** → mora multi-período. Base = `end_date` actual de
  la sub. Nueva cobertura = base + `relativedelta(months=N)`. Reusa la lógica de
  `confirm_renewal_subscription`. Ray con `periodos:2` → cobertura 19/05 → 19/07.
- **Pago selectivo** → el manifest solo incluye las plataformas que el cliente paga.
  Las demás quedan intactas en su estado de deuda, visibles en el panel.
- **`nueva`** → comportamiento actual de compra nueva, ahora dentro del checkout.
- **`cambio`** (solo sub vencida) → libera perfil de `platform_origen_id` con rotación
  de PIN, asigna perfil de `platform_id`, muta el mismo `subscription_id`
  (`platform_id`, `price_usd`, `plan_type`, `end_date` fresco desde aprobación).
  `subtotal_usd = cargo_deuda_usd + precio_unitario_usd`.

---

#### Aritmética de aniversario (meses calendario)

| Cliente | Cubierto hasta | Hoy | Paga | Nueva cobertura | Estado |
|---------|---------------|-----|------|-----------------|--------|
| Ray | 19/05/2026 | 19/06 | 1 mes | 19/06 | sigue debiendo período en curso |
| Ray | 19/05/2026 | 19/06 | 2 meses | 19/07 | al día |

`periodos_vencidos = meses calendario completos entre cobertura actual y hoy`.

**Caso borde fin de mes:** una sub que vence el 31/01 + 1 mes → `relativedelta` la
clampa al 28/02 (o 29 bisiesto), y el mes siguiente vuelve al 31/03. Comportamiento
correcto, documentado para que no parezca bug.

**Auto-corrección del drift:** las subs existentes creadas con base de 30 días adoptan
meses calendario en su próxima renovación. No requiere migración de datos viejos.

---

#### Deuda técnica a corregir durante la implementación

| # | Problema | Archivo | Acción |
|---|----------|---------|--------|
| 1 | Tres cálculos de fecha divergentes; `handle_renewal_cart_confirm` usa `now + timedelta` (desde hoy) en vez de extender desde `end_date` | `subscription.py:905`, `admin.py`, `renovar.py` | Unificar en un helper de fecha único — prerequisito de la mora |
| 2 | `relativedelta` no declarado | `requirements.txt` | Declarar `python-dateutil` explícito |

---

#### Paridad cliente ↔ /renovar

La lógica de cálculo de períodos, mora, construcción de manifest y efectos de
aprobación vive en un **servicio compartido** (`checkout_service`). Tanto el flujo del
cliente como `/renovar` lo invocan — la paridad es automática, no se duplica lógica.
El servicio es agnóstico al almacenamiento de sesión: el cliente persiste en Redis,
`/renovar` en `context.user_data`; cada handler decide dónde guarda la estructura.

---

#### Plan por lotes

| Lote | Descripción | Archivos | Estado |
|------|-------------|----------|--------|
| 0 | Helper Bs en tickets + fix `send_to_admin`→bool | `exchange_service.py`, `notification_service.py`, `router.py`, `dashboard.html` | ✅ Implementado (f672f43, c28112f) |
| 1 | `checkout_service.py`: cálculo períodos/mora/manifest/totales + helper de fecha único que unifica las 3 rutas | `services/checkout_service.py` (nuevo), `subscription.py`, `renovar.py`, `requirements.txt` | 📋 Pendiente |
| 2 | Migración BD: tabla `checkout_orders` (SQL manual en Supabase) | `supabase_schema.sql` + Supabase | 📋 Pendiente |
| 3 | Motor de aprobación: procesa manifest atómicamente (N períodos / nueva / cambio) | `admin.py`, `profiles.py` | 📋 Pendiente |
| 4 | UI cliente: multi-select de perfiles + selector de períodos + total en vivo | `subscription.py`, `keyboards.py` | 📋 Pendiente |
| 5 | Paridad `/renovar`: reconectar al `checkout_service` + motor | `renovar.py` | 📋 Pendiente |
| 6 | Scheduler: desactivar corte automático (PART 2), suspender recordatorios tras D+6 | `scheduler/jobs.py`, `subscriptions.py` | 📋 Pendiente |
| 7 | Docs: `estado_actual.md` + cierre de Fase 8 en `features.md` | — | 📋 Pendiente |

**Dependencias:** Lote 1 es prerequisito de todo (helper de fecha + servicio).
Lote 2 antes de Lote 3 (la tabla debe existir). Lote 5 depende de Lotes 1 y 3.
Lote 6 es independiente y puede ir en paralelo.

**Cambios en BD:** tabla `checkout_orders` (Lote 2). El resto se calcula sobre datos
existentes. Sin migración de subs viejas (drift se autocorrige).

---

## Resumen de todas las fases

| Fase | Descripción | Estado |
|------|-------------|--------|
| ✅ Fase 1 | Reportes mejorados y costos | Implementado |
| ✅ Fase 2 | Afiliación manual mejorada | Implementado |
| ✅ Fase 3 | /renovar para admin | Implementado |
| 📋 Fase 4 | Afiliación y renovación vía Dashboard web | Pendiente |
| 📋 Fase 5 | Migración de cuenta Netflix vía Telegram | Pendiente |
| ⚠️ Fase 6 | Módulo de soporte y tickets + planes Netflix | Parcial — 6A.1 Módulo hogar implementado. Pendiente: 6A.2 soporte por foto, 6B planes Netflix diferenciados |
| 📋 Fase 7 | PWA SmartFlixVE completa | Pendiente |
| 📋 Fase 8 | Pasarela de pagos unificada (mora multi-período, pago selectivo, cambio de plataforma) | Diseñado |

---

## Notas de implementación

- Todas las features de broadcast deben incluir rate limiting (máx 30 mensajes/segundo) para respetar límites de Telegram Bot API
- Las features que añaden columnas a BD requieren migration en Supabase antes de desplegar código
- El scheduler APScheduler ya está configurado — agregar jobs nuevos solo requiere añadir en `setup_scheduler()`
- TMDB API key ya existe en variables de entorno (`TMDB_API_KEY`)
- Gemini API ya está integrado en `services/gemini_service.py`
