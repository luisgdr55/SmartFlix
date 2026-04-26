# StreamVip Bot — Features Planificadas

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
| 10 | Asistente de soporte con IA | Automatización | 🟡 Media | 📋 Pendiente |
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
📊 Reporte semanal StreamVip
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

## Notas de implementación

- Todas las features de broadcast deben incluir rate limiting (máx 30 mensajes/segundo) para respetar límites de Telegram Bot API
- Las features que añaden columnas a BD requieren migration en Supabase antes de desplegar código
- El scheduler APScheduler ya está configurado — agregar jobs nuevos solo requiere añadir en `setup_scheduler()`
- TMDB API key ya existe en variables de entorno (`TMDB_API_KEY`)
- Gemini API ya está integrado en `services/gemini_service.py`
