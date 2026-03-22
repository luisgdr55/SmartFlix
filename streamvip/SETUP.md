# StreamVip Bot - Complete Setup Guide

## Prerequisites

- Python 3.11+
- A Telegram Bot Token (from @BotFather)
- Supabase account (free tier works)
- Upstash Redis account (free tier works)
- Google Gemini API key
- Railway.app account (for deployment)

---

## Step 1: Create Telegram Bot

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow instructions
3. Save the bot token (format: `1234567890:ABCdef...`)
4. Send `/setcommands` and paste:
   ```
   start - Iniciar el bot
   admin - Panel de administración
   tasa - Actualizar tasa Binance
   tasabcv - Actualizar tasas BCV
   addcuenta - Agregar cuenta de streaming
   addexpress - Agregar slot express
   editpin - Editar PIN de perfil
   clientes - Lista de clientes
   cliente - Detalle de cliente
   pendientes - Pagos pendientes
   ingresos - Reporte de ingresos
   bloquear - Bloquear usuario
   broadcast - Mensaje masivo
   flyer - Crear campaña con flyer
   promo - Anunciar contenido
   config - Configuración del sistema
   ```
5. Get your personal Telegram ID using `@userinfobot`

---

## Step 2: Set Up Supabase

1. Go to [supabase.com](https://supabase.com) and create a new project
2. Wait for project to initialize (2-3 minutes)
3. Go to **SQL Editor** in the Supabase dashboard
4. Open `supabase_schema.sql` from this project
5. Paste the entire SQL content and click **Run**
6. Verify tables were created in the **Table Editor**
7. Go to **Settings → API**:
   - Copy the **Project URL** (format: `https://xxxx.supabase.co`)
   - Copy the **service_role key** (NOT the anon key)

---

## Step 3: Set Up Upstash Redis

1. Go to [upstash.com](https://upstash.com) and create a free Redis database
2. Choose the region closest to your Railway deployment
3. After creation, go to **Details** tab
4. Copy the **Redis URL** (format: `redis://default:password@host:port`)

---

## Step 4: Get Google Gemini API Key

1. Go to [aistudio.google.com](https://aistudio.google.com)
2. Click **Get API key**
3. Create a new API key
4. Copy and save it securely

---

## Step 5: Get TMDB API Key (Optional - for content scanning)

1. Go to [themoviedb.org](https://www.themoviedb.org)
2. Create an account and go to **Settings → API**
3. Request an API key (select "Developer" option)
4. Copy the **API Read Access Token** (Bearer token)

---

## Step 6: Set Up Gmail API (Optional - for verification codes)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project
3. Enable **Gmail API**
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client IDs**
5. Application type: **Web application**
6. Add authorized redirect URI: `https://your-app.railway.app/oauth/callback`
7. Download the credentials JSON
8. Note the **Client ID** and **Client Secret**

---

## Step 7: Deploy on Railway

### Option A: Deploy from GitHub

1. Push this code to a GitHub repository
2. Go to [railway.app](https://railway.app) and create a new project
3. Select **Deploy from GitHub repo**
4. Select your repository
5. Railway will auto-detect the `Procfile` and `railway.toml`

### Option B: Deploy with Railway CLI

```bash
npm install -g @railway/cli
railway login
railway init
railway up
```

### Configure Environment Variables on Railway

In Railway dashboard → Your service → **Variables**, add all the following:

```
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_TELEGRAM_IDS=your_telegram_id,second_admin_id
SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_KEY=your_service_role_key
REDIS_URL=redis://default:password@host:port
GEMINI_API_KEY=your_gemini_api_key
TMDB_API_KEY=your_tmdb_api_key
GMAIL_CLIENT_ID=your_gmail_client_id
GMAIL_CLIENT_SECRET=your_gmail_client_secret
GMAIL_REDIRECT_URI=https://your-app.railway.app/oauth/callback
APP_URL=https://your-app.railway.app
WEBHOOK_SECRET=generate_a_random_string_here
DEBUG=false
```

**Generate WEBHOOK_SECRET**: Use any random string, e.g.:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 8: Initial Configuration

Once deployed, configure the bot:

1. **Set exchange rate** - Send to your bot:
   ```
   /tasa 36.50
   ```

2. **Add initial payment config** - Run this SQL in Supabase:
   ```sql
   UPDATE payment_config SET
     banco = 'Banco de Venezuela',
     telefono = '04141234567',
     cedula = 'V-12345678',
     titular = 'Tu Nombre'
   WHERE is_active = TRUE;
   ```

3. **Add streaming accounts** - Use the bot:
   ```
   /addcuenta
   ```
   Follow the interactive prompts.

4. **Add profiles to accounts** - For monthly profiles:
   ```
   /addexpress netflix "Perfil Principal" <account_id>
   ```
   Note: Use your account ID from Supabase or from the bot response.

---

## Step 9: Test the Bot

1. Open Telegram and find your bot
2. Send `/start` - you should see the welcome message
3. Go through a test purchase flow
4. Check Supabase to verify data was created

---

## Maintenance Commands

```
/admin          - View dashboard with all stats
/pendientes     - Review pending payments
/clientes       - List all clients
/cliente 12345  - View specific client details
/ingresos       - Monthly income report
/tasa 36.50     - Update Binance exchange rate
/tasabcv 35.80 38.20  - Update BCV rates
/config         - System configuration overview
```

---

## Monitoring & Logs

- View logs in Railway dashboard → **Deployments → View Logs**
- The `/health` endpoint returns current bot status
- Admin receives daily report at 8:00 AM Venezuela time

---

## Troubleshooting

### Bot not responding
- Check Railway logs for errors
- Verify `TELEGRAM_BOT_TOKEN` is correct
- Check webhook is set: `https://api.telegram.org/bot{TOKEN}/getWebhookInfo`

### Database errors
- Verify `SUPABASE_URL` and `SUPABASE_KEY` are correct
- Ensure schema was executed successfully
- Check Supabase logs in the dashboard

### Redis connection errors
- Verify `REDIS_URL` format: `redis://default:password@host:port`
- Check Upstash dashboard for connection status

### Payment validation not working
- Verify `GEMINI_API_KEY` is valid
- Check Gemini API quota in Google AI Studio
- Image must be clear and recent (within 60 minutes)

---

## Architecture Overview

```
FastAPI (main.py)
    ├── /webhook - Receives Telegram updates
    ├── /health  - Health check
    │
    ├── Telegram Bot (python-telegram-bot)
    │   ├── Handlers (bot/handlers/)
    │   ├── Keyboards (bot/keyboards.py)
    │   ├── Messages (bot/messages.py)
    │   └── Middleware (bot/middleware.py)
    │
    ├── Services
    │   ├── Gemini AI (vision + text)
    │   ├── TMDB (content discovery)
    │   ├── Gmail (verification codes)
    │   ├── Exchange (USD/Bs rates)
    │   ├── Payment (validation)
    │   ├── Flyer (image generation)
    │   └── Notifications (messaging)
    │
    ├── Database (Supabase/PostgreSQL)
    │   ├── Users, Platforms, Accounts
    │   ├── Profiles, Subscriptions
    │   └── Analytics, Campaigns
    │
    ├── Cache (Redis/Upstash)
    │   ├── User states
    │   ├── Exchange rates (30min TTL)
    │   └── Conversation context (2h TTL)
    │
    └── Scheduler (APScheduler)
        ├── Expiry reminders (daily 10AM)
        ├── Expiry notifications (hourly)
        ├── Express release (15min)
        ├── Queue cleanup (daily 3AM)
        ├── New releases scan (Mon+Thu 9AM)
        ├── Pending cleanup (45min)
        └── Daily report (8AM)
```

---

## Security Notes

- Never commit `.env` files to version control
- Use strong random `WEBHOOK_SECRET`
- The `SUPABASE_KEY` is your service_role key - keep it secret
- Admin IDs are validated on every admin command
- Payment images are hash-checked to prevent duplicate submissions
- Rate limiting: 30 messages per minute per user
