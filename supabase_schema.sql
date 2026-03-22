-- StreamVip Bot - Complete Supabase Schema
-- Run this in your Supabase SQL editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- USERS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    name VARCHAR(255),
    phone VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    is_admin BOOLEAN DEFAULT FALSE,
    total_purchases INT DEFAULT 0,
    status VARCHAR(50) DEFAULT 'active',
    preferred_platform VARCHAR(100),
    receives_promos BOOLEAN DEFAULT TRUE,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_receives_promos ON users(receives_promos);

-- ============================================================
-- PLATFORMS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS platforms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    slug VARCHAR(50) UNIQUE NOT NULL,
    icon_emoji VARCHAR(10),
    color_hex VARCHAR(7),
    monthly_price_usd DECIMAL(10,2),
    express_price_usd DECIMAL(10,2) DEFAULT 1.00,
    week_price_usd DECIMAL(10,2),
    max_profiles INT DEFAULT 5,
    is_active BOOLEAN DEFAULT TRUE,
    instructions_monthly TEXT,
    instructions_express TEXT,
    tmdb_provider_id INT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_platforms_slug ON platforms(slug);
CREATE INDEX IF NOT EXISTS idx_platforms_is_active ON platforms(is_active);

-- ============================================================
-- ACCOUNTS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform_id UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL,
    billing_date DATE,
    gmail_api_enabled BOOLEAN DEFAULT FALSE,
    gmail_credentials JSONB,
    status VARCHAR(50) DEFAULT 'active',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_platform_id ON accounts(platform_id);
CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts(status);

-- ============================================================
-- PROFILES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    platform_id UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    profile_name VARCHAR(100) NOT NULL,
    pin VARCHAR(10),
    profile_type VARCHAR(50) DEFAULT 'monthly',
    status VARCHAR(50) DEFAULT 'available',
    last_released TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profiles_account_id ON profiles(account_id);
CREATE INDEX IF NOT EXISTS idx_profiles_platform_id ON profiles(platform_id);
CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_type_status ON profiles(profile_type, status);

-- ============================================================
-- SUBSCRIPTIONS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    platform_id UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    plan_type VARCHAR(50) NOT NULL,
    start_date TIMESTAMPTZ DEFAULT NOW(),
    end_date TIMESTAMPTZ NOT NULL,
    price_usd DECIMAL(10,2),
    price_bs DECIMAL(10,2),
    rate_used DECIMAL(10,2),
    status VARCHAR(50) DEFAULT 'pending_payment',
    payment_reference VARCHAR(255),
    payment_image_url TEXT,
    payment_confirmed_at TIMESTAMPTZ,
    reminder_sent BOOLEAN DEFAULT FALSE,
    expiry_notified BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_profile_id ON subscriptions(profile_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_platform_id ON subscriptions(platform_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_end_date ON subscriptions(end_date);

-- ============================================================
-- PAYMENT CONFIG TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS payment_config (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    banco VARCHAR(100),
    telefono VARCHAR(20),
    cedula VARCHAR(20),
    titular VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- EXCHANGE RATES TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS exchange_rates (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    usd_binance DECIMAL(10,4),
    usd_bcv DECIMAL(10,4),
    eur_bcv DECIMAL(10,4),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by BIGINT
);

CREATE INDEX IF NOT EXISTS idx_exchange_rates_updated_at ON exchange_rates(updated_at DESC);

-- ============================================================
-- EXPRESS QUEUE TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS express_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform_id UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    status VARCHAR(50) DEFAULT 'waiting',
    notified_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE INDEX IF NOT EXISTS idx_express_queue_user_id ON express_queue(user_id);
CREATE INDEX IF NOT EXISTS idx_express_queue_platform_id ON express_queue(platform_id);
CREATE INDEX IF NOT EXISTS idx_express_queue_status ON express_queue(status);
CREATE INDEX IF NOT EXISTS idx_express_queue_expires_at ON express_queue(expires_at);

-- ============================================================
-- CAMPAIGNS TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title VARCHAR(255) NOT NULL,
    platform_id UUID REFERENCES platforms(id) ON DELETE SET NULL,
    content_title VARCHAR(255),
    content_type VARCHAR(50),
    content_year INT,
    synopsis_vzla TEXT,
    flyer_image_url TEXT,
    audience VARCHAR(100) DEFAULT 'all',
    sent_count INT DEFAULT 0,
    clicked_count INT DEFAULT 0,
    converted_count INT DEFAULT 0,
    scheduled_for TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    created_by BIGINT,
    status VARCHAR(50) DEFAULT 'draft',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_campaigns_platform_id ON campaigns(platform_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_status ON campaigns(status);
CREATE INDEX IF NOT EXISTS idx_campaigns_scheduled_for ON campaigns(scheduled_for);

-- ============================================================
-- ADMIN LOG TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS admin_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_telegram_id BIGINT NOT NULL,
    action VARCHAR(255) NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_log_admin_telegram_id ON admin_log(admin_telegram_id);
CREATE INDEX IF NOT EXISTS idx_admin_log_created_at ON admin_log(created_at DESC);

-- ============================================================
-- ANNOUNCED CONTENT TABLE
-- ============================================================
CREATE TABLE IF NOT EXISTS announced_content (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tmdb_id INT NOT NULL,
    title VARCHAR(255),
    platform_id UUID REFERENCES platforms(id) ON DELETE SET NULL,
    announced_at TIMESTAMPTZ DEFAULT NOW(),
    do_not_repeat_until TIMESTAMPTZ DEFAULT NOW() + INTERVAL '30 days'
);

CREATE INDEX IF NOT EXISTS idx_announced_content_tmdb_id ON announced_content(tmdb_id);
CREATE INDEX IF NOT EXISTS idx_announced_content_platform_id ON announced_content(platform_id);
CREATE INDEX IF NOT EXISTS idx_announced_content_do_not_repeat ON announced_content(do_not_repeat_until);

-- ============================================================
-- SEED DATA - Default payment config
-- ============================================================
INSERT INTO payment_config (banco, telefono, cedula, titular, is_active)
VALUES ('Banco de Venezuela', '04141234567', 'V-12345678', 'StreamVip Venezuela', TRUE)
ON CONFLICT DO NOTHING;

-- ============================================================
-- SEED DATA - Sample platforms
-- ============================================================
INSERT INTO platforms (name, slug, icon_emoji, color_hex, monthly_price_usd, express_price_usd, week_price_usd, max_profiles, is_active, tmdb_provider_id)
VALUES
    ('Netflix', 'netflix', '🎬', '#E50914', 4.50, 1.00, 2.50, 5, TRUE, 8),
    ('Disney+', 'disney', '✨', '#113CCF', 4.00, 1.00, 2.00, 5, TRUE, 337),
    ('Max', 'max', '💜', '#5822A9', 4.50, 1.00, 2.50, 5, TRUE, 1843),
    ('Paramount+', 'paramount', '⭐', '#0064FF', 3.50, 1.00, 2.00, 5, TRUE, 531),
    ('Prime Video', 'prime', '🎯', '#00A8E0', 3.50, 1.00, 2.00, 5, TRUE, 119)
ON CONFLICT (slug) DO NOTHING;
