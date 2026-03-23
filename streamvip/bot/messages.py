from __future__ import annotations

WELCOME_NEW_USER = (
    "¡Hola! 👋 Bienvenido/a a <b>StreamVip Venezuela</b> 🇻🇪\n\n"
    "Somos tu servicio de confianza para perfiles de streaming:\n"
    "🎬 Netflix • ✨ Disney+ • 💜 Max • ⭐ Paramount+ • 🎯 Prime Video\n\n"
    "Para empezar, <b>¿cómo te llamas?</b> 😊"
)

NAME_REQUEST = "¿Cómo te llamas? Solo escribe tu nombre 👇"

NAME_CONFIRMED = (
    "¡Perfecto, <b>{name}</b>! 🎉\n\n"
    "Ya estás listo/a para disfrutar del mejor streaming de Venezuela. "
    "¿Qué quieres hacer hoy?"
)

MAIN_MENU = (
    "🏠 <b>Menú Principal</b>\n\n"
    "¡Hola, <b>{name}</b>! 👋 ¿Qué necesitas hoy?\n\n"
    "📊 <i>Disponibilidad actual:</i>\n{availability}\n\n"
    "Selecciona una opción:"
)

SUBSCRIPTION_PLATFORM_SELECT = (
    "📺 <b>Suscripción Mensual</b>\n\n"
    "Elige la plataforma que deseas:\n\n"
    "{platform_list}\n\n"
    "💡 <i>Incluye acceso completo por 30 días</i>"
)

SUBSCRIPTION_CONFIRM = (
    "✅ <b>Confirmar Pedido</b>\n\n"
    "📺 Plataforma: <b>{platform}</b>\n"
    "📅 Plan: <b>Mensual (30 días)</b>\n"
    "💵 Precio: <b>{price_usd}</b> = <b>{price_bs}</b>\n"
    "📊 Tasa: Bs {rate}/USD\n\n"
    "¿Confirmamos tu pedido?"
)

PAYMENT_INSTRUCTIONS = (
    "💳 <b>Instrucciones de Pago</b>\n\n"
    "Realiza tu pago por <b>Pago Móvil</b>:\n\n"
    "🏦 <b>Banco:</b> {banco}\n"
    "📱 <b>Teléfono:</b> {telefono}\n"
    "🪪 <b>Cédula:</b> {cedula}\n"
    "👤 <b>Titular:</b> {titular}\n\n"
    "💰 <b>Monto exacto:</b> <code>Bs {amount_bs}</code>\n\n"
    "⏰ Tienes <b>30 minutos</b> para realizar el pago.\n\n"
    "Una vez pagado, <b>envía el comprobante como foto</b> aquí. 📸\n\n"
    "🔖 <i>Ref. pedido: #{order_id}</i>"
)

PAYMENT_CONFIRMED = (
    "✅ <b>¡Pago confirmado!</b>\n\n"
    "Tu suscripción ha sido activada con éxito 🎉\n\n"
    "📺 <b>Plataforma:</b> {platform}\n"
    "📅 <b>Vigencia:</b> {start_date} → {end_date}\n"
    "🔖 <b>Referencia:</b> #{reference}\n\n"
    "A continuación tus datos de acceso 👇"
)

ACCESS_DELIVERED = (
    "🔐 <b>Tus datos de acceso</b>\n\n"
    "📺 <b>{platform}</b>\n"
    "👤 <b>Perfil:</b> {profile_name}\n"
    "📧 <b>Cuenta:</b> <code>{email}</code>\n"
    "🔑 <b>Contraseña:</b> <code>{password}</code>\n"
    "{pin_line}"
    "\n"
    "📋 <b>Instrucciones:</b>\n{instructions}\n\n"
    "⚠️ <i>No compartas estos datos. Si tienes problemas usa /soporte</i>"
)

PIN_LINE = "🔢 <b>PIN del perfil:</b> <code>{pin}</code>\n"

ACCESS_INSTRUCTIONS = {
    "netflix": (
        "1️⃣ Abre Netflix y ve a 'Cambiar perfil'\n"
        "2️⃣ Selecciona el perfil <b>{profile_name}</b>\n"
        "3️⃣ Si pide PIN, ingresa el código enviado\n"
        "4️⃣ ¡Disfruta! 🍿"
    ),
    "disney": (
        "1️⃣ Inicia sesión con el email y contraseña\n"
        "2️⃣ Selecciona el perfil <b>{profile_name}</b>\n"
        "3️⃣ Si pide código de verificación, avísanos\n"
        "4️⃣ ¡A ver contenido! ✨"
    ),
    "max": (
        "1️⃣ Ingresa con email y contraseña\n"
        "2️⃣ Selecciona el perfil <b>{profile_name}</b>\n"
        "3️⃣ Si solicita verificación, usa /soporte\n"
        "4️⃣ ¡Disfruta! 💜"
    ),
    "paramount": (
        "1️⃣ Inicia sesión con el email y contraseña\n"
        "2️⃣ Selecciona el perfil <b>{profile_name}</b>\n"
        "3️⃣ Verifica en tu email si pide confirmación\n"
        "4️⃣ ¡A disfrutar! ⭐"
    ),
    "prime": (
        "1️⃣ Entra a Amazon Prime Video\n"
        "2️⃣ Inicia sesión con el email y contraseña\n"
        "3️⃣ Selecciona el perfil <b>{profile_name}</b>\n"
        "4️⃣ ¡Listo para ver! 🎯"
    ),
}

EXPRESS_PLATFORM_SELECT = (
    "⚡ <b>Express 24 Horas</b>\n\n"
    "Acceso inmediato por solo <b>$1 USD</b> 🚀\n\n"
    "Elige la plataforma:\n\n{platform_list}\n\n"
    "💡 <i>Perfecto para ver una película o maratonear una serie</i>"
)

EXPRESS_NO_STOCK = (
    "😔 <b>Sin disponibilidad Express</b>\n\n"
    "En este momento no tenemos slots Express disponibles para <b>{platform}</b>.\n\n"
    "¿Qué deseas hacer?\n"
    "• Unirte a la lista de espera (te notificamos cuando haya)\n"
    "• Elegir otra plataforma\n"
    "• Tomar un plan mensual"
)

EXPRESS_DELIVERED = (
    "⚡ <b>¡Acceso Express Activado!</b>\n\n"
    "📺 <b>{platform}</b> por 24 horas\n"
    "⏰ <b>Expira:</b> {end_date}\n\n"
    "Tus datos de acceso están arriba ☝️\n\n"
    "💡 <i>Cuando veas una buena película, considera el plan mensual 😉</i>"
)

EXPRESS_EXPIRED = (
    "⏰ <b>Tu acceso Express ha expirado</b>\n\n"
    "Hola <b>{name}</b>, tu acceso de 24h a <b>{platform}</b> ha terminado.\n\n"
    "¿Qué tal estuvo? 🍿\n\n"
    "Si quieres seguir disfrutando, ¡pasa al <b>plan mensual</b>! "
    "Costo mínimo, entretenimiento máximo 💪\n\n"
    "Usa el botón de abajo para renovar 👇"
)

WEEK_PACK_EXPIRY_REMINDER = (
    "📅 <b>Tu pack semanal vence pronto</b>\n\n"
    "Hola <b>{name}</b> 👋\n\n"
    "Tu suscripción a <b>{platform}</b> vence el <b>{end_date}</b> "
    "({days} días restantes).\n\n"
    "¿Renovamos? 🎬"
)

MY_SERVICES_ACTIVE = (
    "📋 <b>Mis Servicios Activos</b>\n\n"
    "Tienes <b>{count}</b> servicio(s) activo(s):\n\n"
    "{services_list}"
)

MY_SERVICES_EMPTY = (
    "📋 <b>Mis Servicios</b>\n\n"
    "No tienes servicios activos en este momento.\n\n"
    "¿Quieres contratar uno? Usa el menú principal 👇"
)

SUPPORT_MENU = (
    "🆘 <b>Centro de Soporte</b>\n\n"
    "¿En qué te podemos ayudar?\n\n"
    "Elige una opción:"
)

SUPPORT_NO_CREDENTIALS = (
    "📧 <b>Reenvío de Credenciales</b>\n\n"
    "Aquí están tus datos de acceso para <b>{platform}</b>:\n\n"
    "👤 <b>Perfil:</b> {profile_name}\n"
    "📧 <b>Email:</b> <code>{email}</code>\n"
    "🔑 <b>Contraseña:</b> <code>{password}</code>\n"
    "{pin_line}\n"
    "Si el problema persiste, contáctanos directamente."
)

SUPPORT_VERIFICATION_CODE = (
    "🔐 <b>Código de Verificación</b>\n\n"
    "Buscando el código de verificación para tu cuenta de <b>{platform}</b>...\n\n"
    "⏳ Esto puede tardar unos segundos."
)

SUPPORT_CODE_FOUND = (
    "✅ <b>Código encontrado</b>\n\n"
    "Tu código de verificación para <b>{platform}</b> es:\n\n"
    "<code>{code}</code>\n\n"
    "⚠️ <i>Este código expira en pocos minutos. Úsalo ahora.</i>"
)

SUPPORT_CODE_NOT_FOUND = (
    "❌ <b>No se encontró código</b>\n\n"
    "No encontramos un código reciente para tu cuenta de <b>{platform}</b>.\n\n"
    "Intenta solicitar un nuevo código directamente en la app o contacta a soporte."
)

EXPIRY_REMINDER_3DAYS = (
    "⏰ <b>Recordatorio de Vencimiento</b>\n\n"
    "Hola <b>{name}</b> 👋\n\n"
    "Tu suscripción a <b>{platform}</b> vence el <b>{end_date}</b> "
    "({days} días).\n\n"
    "¿Renovamos para que no pierdas el acceso? 🎬\n\n"
    "Usa el botón de abajo 👇"
)

EXPIRY_NOTIFICATION = (
    "😔 <b>Tu suscripción ha vencido</b>\n\n"
    "Hola <b>{name}</b>, tu acceso a <b>{platform}</b> ha expirado.\n\n"
    "¡Pero tranquilo/a! Puedes renovar ahora mismo y seguir disfrutando 🎬\n\n"
    "Presiona el botón para renovar 👇"
)

SOFT_CUT_NOTIFICATION = (
    "🔒 <b>Acceso suspendido temporalmente</b>\n\n"
    "Hola <b>{name}</b>, tu suscripción a <b>{platform}</b> venció el <b>{end_date}</b> "
    "y tu acceso ha sido suspendido.\n\n"
    "Si deseas renovar y recuperar el acceso, usa el botón de abajo 👇\n\n"
    "<i>Si ya realizaste el pago, contáctanos por soporte.</i>"
)

PROFILE_RELEASED_NOTIFICATION = (
    "📤 <b>Suscripción finalizada</b>\n\n"
    "Hola <b>{name}</b>, tu suscripción a <b>{platform}</b> ha finalizado "
    "y tu perfil ha sido liberado.\n\n"
    "¡Esperamos haberte brindado un excelente servicio! 🎬\n\n"
    "Cuando quieras volver, aquí estaremos 😊\n"
    "Usa el botón de abajo para contratar un nuevo plan 👇"
)

PAYMENT_EXPIRED = (
    "⏰ <b>Tiempo de pago expirado</b>\n\n"
    "Tu pedido #{order_id} ha sido cancelado por no recibir el pago a tiempo.\n\n"
    "Si deseas hacer el pedido nuevamente, usa el menú principal. "
    "Tendrás 30 minutos para completar el pago."
)

PAYMENT_INVALID = (
    "❌ <b>Comprobante no válido</b>\n\n"
    "<b>Motivo:</b> {reason}\n\n"
    "Por favor verifica:\n"
    "• Que el monto sea exacto: <b>Bs {amount_bs}</b>\n"
    "• Que la foto sea clara y legible\n"
    "• Que sea un pago reciente (máx. 60 min)\n\n"
    "Envía nuevamente el comprobante o contacta a soporte."
)

ADMIN_DASHBOARD = (
    "🎛️ <b>Panel de Administración - StreamVip</b>\n\n"
    "📊 <b>Estadísticas del día:</b>\n"
    "👥 Usuarios totales: <b>{total_users}</b>\n"
    "🆕 Nuevos hoy: <b>{new_users_today}</b>\n"
    "✅ Suscripciones activas: <b>{active_subscriptions}</b>\n"
    "⏳ Pagos pendientes: <b>{pending_payments}</b>\n"
    "⚠️ Vencen en 3 días: <b>{expiring_soon}</b>\n"
    "💵 Ingresos este mes: <b>${monthly_revenue_usd:.2f} USD</b>\n\n"
    "📦 <b>Disponibilidad por plataforma:</b>\n{availability}\n\n"
    "Usa los botones para gestionar:"
)

ERROR_GENERIC = (
    "😅 Ocurrió un error inesperado. Por favor intenta de nuevo.\n"
    "Si el problema persiste, contacta a soporte."
)

TROUBLESHOOTING = {
    "netflix": (
        "🔧 <b>Solución de Problemas - Netflix</b>\n\n"
        "1️⃣ Cierra y vuelve a abrir la app\n"
        "2️⃣ Verifica que estés en el perfil correcto\n"
        "3️⃣ Si pide código, usa /soporte → Código de verificación\n"
        "4️⃣ Borra caché de la app si sigue fallando\n"
        "5️⃣ Reinstala la app como último recurso\n\n"
        "¿Sigue el problema? Escríbenos ➡️"
    ),
    "disney": (
        "🔧 <b>Solución de Problemas - Disney+</b>\n\n"
        "1️⃣ Cierra y vuelve a abrir la app\n"
        "2️⃣ Asegúrate de usar el perfil asignado\n"
        "3️⃣ Si pide verificación de email, usa /soporte\n"
        "4️⃣ Verifica tu conexión a internet\n"
        "5️⃣ Prueba en otro dispositivo\n\n"
        "¿Sigue el problema? Escríbenos ➡️"
    ),
    "max": (
        "🔧 <b>Solución de Problemas - Max</b>\n\n"
        "1️⃣ Cierra sesión y vuelve a iniciar\n"
        "2️⃣ Usa exactamente el email y contraseña enviados\n"
        "3️⃣ Si pide código, usa /soporte → Código de verificación\n"
        "4️⃣ Borra caché de la app\n"
        "5️⃣ Prueba desde navegador web si la app falla\n\n"
        "¿Sigue el problema? Escríbenos ➡️"
    ),
    "paramount": (
        "🔧 <b>Solución de Problemas - Paramount+</b>\n\n"
        "1️⃣ Verifica el email y contraseña\n"
        "2️⃣ Si pide verificación, revisa el correo\n"
        "3️⃣ Cierra sesión en otros dispositivos si hay error de límite\n"
        "4️⃣ Borra caché y datos de la app\n"
        "5️⃣ Contáctanos si persiste\n\n"
        "¿Sigue el problema? Escríbenos ➡️"
    ),
    "prime": (
        "🔧 <b>Solución de Problemas - Prime Video</b>\n\n"
        "1️⃣ Inicia sesión en amazon.com primero\n"
        "2️⃣ Luego accede a Prime Video\n"
        "3️⃣ Si pide OTP, usa /soporte → Código\n"
        "4️⃣ Verifica que la región esté en Venezuela o usa VPN\n"
        "5️⃣ Borra caché si la app no carga\n\n"
        "¿Sigue el problema? Escríbenos ➡️"
    ),
}

SERVICE_DETAIL = (
    "{icon} <b>{platform}</b>\n"
    "📅 Plan: <b>{plan_type}</b>\n"
    "👤 Perfil: <b>{profile_name}</b>\n"
    "⏰ Vence: <b>{end_date}</b> ({days_left} días)\n"
    "🔖 ID: <code>#{sub_id}</code>\n"
)
