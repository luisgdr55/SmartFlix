"""StreamVip Admin Panel — FastAPI router with all panel routes."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Response, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from admin_panel.auth import (
    create_session, clear_session, verify_session, verify_password,
    _AuthRedirectException,
)

logger = logging.getLogger(__name__)

# ── Templates ─────────────────────────────────────────────────────────────────
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ── Router ────────────────────────────────────────────────────────────────────
panel_router = APIRouter(prefix="/panel", tags=["admin_panel"])


# ── Auth guard helper ─────────────────────────────────────────────────────────
def _auth_guard(request: Request) -> Optional[RedirectResponse]:
    """Return a RedirectResponse if not authenticated, else None."""
    if not verify_session(request):
        return RedirectResponse(url="/panel/login", status_code=302)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if verify_session(request):
        return RedirectResponse(url="/panel/dashboard", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@panel_router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, password: str = Form(...)):
    if verify_password(password):
        response = RedirectResponse(url="/panel/dashboard", status_code=302)
        create_session(response)
        return response
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Contraseña incorrecta. Intenta de nuevo."},
        status_code=401,
    )


@panel_router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/panel/login", status_code=302)
    clear_session(response)
    return response


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("", response_class=HTMLResponse)
@panel_router.get("/", response_class=HTMLResponse)
async def panel_root(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    return RedirectResponse(url="/panel/dashboard", status_code=302)


@panel_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.analytics import get_dashboard_stats
        from database.subscriptions import get_pending_subscriptions
        from services.exchange_service import get_current_rate

        stats = await get_dashboard_stats()
        pending = await get_pending_subscriptions()
        rate_data = await get_current_rate()

        # Last 5 subscriptions
        from database import get_supabase
        sb = get_supabase()
        last_subs = sb.table("subscriptions").select(
            "*, users(name, username), platforms(name, icon_emoji)"
        ).order("created_at", desc=True).limit(5).execute()

        # Revenue last 7 days for chart
        from utils.helpers import venezuela_now
        now = venezuela_now()
        daily_labels = []
        daily_values = []
        for i in range(6, -1, -1):
            day = now - timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
            rev_res = sb.table("subscriptions").select("price_usd").in_(
                "status", ["active", "expired"]
            ).gte("payment_confirmed_at", day_start.isoformat()).lte(
                "payment_confirmed_at", day_end.isoformat()
            ).execute()
            daily_total = sum(r.get("price_usd", 0) or 0 for r in (rev_res.data or []))
            daily_labels.append(day.strftime("%d/%m"))
            daily_values.append(round(daily_total, 2))

        # Express active count
        express_result = sb.table("subscriptions").select("id", count="exact").eq(
            "status", "active"
        ).eq("plan_type", "express").execute()
        express_active = express_result.count or 0

        context = {
            "request": request,
            "page": "dashboard",
            "stats": stats,
            "pending_count": len(pending),
            "rate_data": rate_data,
            "last_subs": last_subs.data or [],
            "express_active": express_active,
            "daily_labels": daily_labels,
            "daily_values": daily_values,
            "platform_availability": stats.get("platform_availability", []),
        }
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        context = {
            "request": request,
            "page": "dashboard",
            "stats": {},
            "pending_count": 0,
            "rate_data": None,
            "last_subs": [],
            "express_active": 0,
            "daily_labels": [],
            "daily_values": [],
            "platform_availability": [],
            "error": str(e),
        }
    return templates.TemplateResponse("dashboard.html", context)


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNTS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/accounts", response_class=HTMLResponse)
async def accounts_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from database.platforms import get_active_platforms
        sb = get_supabase()
        accounts_res = sb.table("accounts").select(
            "*, platforms(name, icon_emoji, slug)"
        ).order("created_at", desc=True).execute()
        platforms = await get_active_platforms()
        # Attach profile count per account
        accounts = accounts_res.data or []
        for acc in accounts:
            profiles_res = sb.table("profiles").select("id", count="exact").eq(
                "account_id", acc["id"]
            ).execute()
            acc["profile_count"] = profiles_res.count or 0
    except Exception as e:
        logger.error(f"Accounts list error: {e}")
        accounts = []
        platforms = []
    return templates.TemplateResponse("accounts.html", {
        "request": request,
        "page": "accounts",
        "accounts": accounts,
        "platforms": platforms,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.get("/accounts/new", response_class=HTMLResponse)
async def account_new_form(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    from database.platforms import get_active_platforms
    platforms = await get_active_platforms()
    return templates.TemplateResponse("account_form.html", {
        "request": request,
        "page": "accounts",
        "platforms": platforms,
        "account": None,
        "form_action": "/panel/accounts/new",
        "form_title": "Nueva Cuenta",
    })


@panel_router.post("/accounts/new")
async def account_new_save(
    request: Request,
    platform_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    billing_date: str = Form(default=""),
    notes: str = Form(default=""),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        data = {
            "platform_id": platform_id,
            "email": email,
            "password": password,
            "status": "active",
        }
        if billing_date:
            data["billing_date"] = billing_date
        if notes:
            data["notes"] = notes
        sb.table("accounts").insert(data).execute()
        return RedirectResponse(url="/panel/accounts?success=Cuenta+creada+exitosamente", status_code=302)
    except Exception as e:
        logger.error(f"Account create error: {e}")
        return RedirectResponse(url=f"/panel/accounts?error={str(e)[:100]}", status_code=302)


@panel_router.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
async def account_edit_form(request: Request, account_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.accounts import get_account_by_id
        from database.platforms import get_active_platforms
        account = await get_account_by_id(account_id)
        platforms = await get_active_platforms()
        if not account:
            return RedirectResponse(url="/panel/accounts?error=Cuenta+no+encontrada", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/accounts?error={str(e)[:100]}", status_code=302)
    return templates.TemplateResponse("account_form.html", {
        "request": request,
        "page": "accounts",
        "platforms": platforms,
        "account": account,
        "form_action": f"/panel/accounts/{account_id}/edit",
        "form_title": "Editar Cuenta",
    })


@panel_router.post("/accounts/{account_id}/edit")
async def account_edit_save(
    request: Request,
    account_id: str,
    platform_id: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    billing_date: str = Form(default=""),
    notes: str = Form(default=""),
    status: str = Form(default="active"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        data = {
            "platform_id": platform_id,
            "email": email,
            "password": password,
            "status": status,
        }
        if billing_date:
            data["billing_date"] = billing_date
        if notes:
            data["notes"] = notes
        sb.table("accounts").update(data).eq("id", account_id).execute()
        return RedirectResponse(url="/panel/accounts?success=Cuenta+actualizada", status_code=302)
    except Exception as e:
        logger.error(f"Account edit error: {e}")
        return RedirectResponse(url=f"/panel/accounts?error={str(e)[:100]}", status_code=302)


@panel_router.post("/accounts/{account_id}/delete")
async def account_delete(request: Request, account_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("accounts").delete().eq("id", account_id).execute()
        return RedirectResponse(url="/panel/accounts?success=Cuenta+eliminada", status_code=302)
    except Exception as e:
        logger.error(f"Account delete error: {e}")
        return RedirectResponse(url=f"/panel/accounts?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILES
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/profiles", response_class=HTMLResponse)
async def profiles_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from database.platforms import get_active_platforms
        sb = get_supabase()
        query = sb.table("profiles").select(
            "*, accounts(email), platforms(name, icon_emoji, slug)"
        )
        platform_filter = request.query_params.get("platform")
        tipo_filter = request.query_params.get("tipo")
        estado_filter = request.query_params.get("estado")
        if platform_filter:
            query = query.eq("platform_id", platform_filter)
        if tipo_filter:
            query = query.eq("profile_type", tipo_filter)
        if estado_filter:
            query = query.eq("status", estado_filter)
        result = query.order("created_at", desc=True).execute()
        profiles = result.data or []
        platforms = await get_active_platforms()

        # Attach active subscription (end_date + user) to each occupied profile
        occupied_ids = [p["id"] for p in profiles if p.get("status") == "occupied"]
        subs_map: dict = {}
        if occupied_ids:
            subs_res = sb.table("subscriptions").select(
                "profile_id, end_date, plan_type, users(name, username)"
            ).in_("profile_id", occupied_ids).eq("status", "active").execute()
            for s in (subs_res.data or []):
                subs_map[s["profile_id"]] = s
        for p in profiles:
            p["_active_sub"] = subs_map.get(p["id"])
    except Exception as e:
        logger.error(f"Profiles list error: {e}")
        profiles = []
        platforms = []
    return templates.TemplateResponse("profiles.html", {
        "request": request,
        "page": "profiles",
        "profiles": profiles,
        "platforms": platforms,
        "platform_filter": request.query_params.get("platform", ""),
        "tipo_filter": request.query_params.get("tipo", ""),
        "estado_filter": request.query_params.get("estado", ""),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.get("/profiles/new", response_class=HTMLResponse)
async def profile_new_form(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    from database.platforms import get_active_platforms
    from database import get_supabase
    platforms = await get_active_platforms()
    sb = get_supabase()
    accounts_res = sb.table("accounts").select("id, email, platform_id, platforms(name)").execute()
    return templates.TemplateResponse("profile_form.html", {
        "request": request,
        "page": "profiles",
        "platforms": platforms,
        "accounts": accounts_res.data or [],
        "profile": None,
        "form_action": "/panel/profiles/new",
        "form_title": "Nuevo Perfil",
    })


@panel_router.post("/profiles/new")
async def profile_new_save(
    request: Request,
    account_id: str = Form(...),
    platform_id: str = Form(...),
    profile_name: str = Form(...),
    pin: str = Form(default=""),
    profile_type: str = Form(default="monthly"),
    status: str = Form(default="available"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("profiles").insert({
            "account_id": account_id,
            "platform_id": platform_id,
            "profile_name": profile_name,
            "pin": pin or None,
            "profile_type": profile_type,
            "status": status,
        }).execute()
        return RedirectResponse(url="/panel/profiles?success=Perfil+creado+exitosamente", status_code=302)
    except Exception as e:
        logger.error(f"Profile create error: {e}")
        return RedirectResponse(url=f"/panel/profiles?error={str(e)[:100]}", status_code=302)


@panel_router.get("/profiles/{profile_id}/edit", response_class=HTMLResponse)
async def profile_edit_form(request: Request, profile_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.profiles import get_profile_by_id
        from database.platforms import get_active_platforms
        from database import get_supabase
        profile = await get_profile_by_id(profile_id)
        platforms = await get_active_platforms()
        sb = get_supabase()
        accounts_res = sb.table("accounts").select("id, email, platform_id, platforms(name)").execute()
        if not profile:
            return RedirectResponse(url="/panel/profiles?error=Perfil+no+encontrado", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/profiles?error={str(e)[:100]}", status_code=302)
    return templates.TemplateResponse("profile_form.html", {
        "request": request,
        "page": "profiles",
        "platforms": platforms,
        "accounts": accounts_res.data or [],
        "profile": profile,
        "form_action": f"/panel/profiles/{profile_id}/edit",
        "form_title": "Editar Perfil",
    })


@panel_router.post("/profiles/{profile_id}/edit")
async def profile_edit_save(
    request: Request,
    profile_id: str,
    account_id: str = Form(...),
    platform_id: str = Form(...),
    profile_name: str = Form(...),
    pin: str = Form(default=""),
    profile_type: str = Form(default="monthly"),
    status: str = Form(default="available"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("profiles").update({
            "account_id": account_id,
            "platform_id": platform_id,
            "profile_name": profile_name,
            "pin": pin or None,
            "profile_type": profile_type,
            "status": status,
        }).eq("id", profile_id).execute()
        return RedirectResponse(url="/panel/profiles?success=Perfil+actualizado", status_code=302)
    except Exception as e:
        logger.error(f"Profile edit error: {e}")
        return RedirectResponse(url=f"/panel/profiles?error={str(e)[:100]}", status_code=302)


@panel_router.post("/profiles/{profile_id}/delete")
async def profile_delete(request: Request, profile_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("profiles").delete().eq("id", profile_id).execute()
        return RedirectResponse(url="/panel/profiles?success=Perfil+eliminado", status_code=302)
    except Exception as e:
        logger.error(f"Profile delete error: {e}")
        return RedirectResponse(url=f"/panel/profiles?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# USERS / CLIENTS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/users", response_class=HTMLResponse)
async def users_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        page = int(request.query_params.get("page", 1))
        search = request.query_params.get("q", "").strip()
        per_page = 20
        offset = (page - 1) * per_page

        query = sb.table("users").select("*")
        if search:
            query = query.or_(f"name.ilike.%{search}%,username.ilike.%{search}%")

        count_query = sb.table("users").select("id", count="exact")
        if search:
            count_query = count_query.or_(f"name.ilike.%{search}%,username.ilike.%{search}%")
        count_res = count_query.execute()
        total = count_res.count or 0

        result = query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
        users = result.data or []
        total_pages = (total + per_page - 1) // per_page
    except Exception as e:
        logger.error(f"Users list error: {e}")
        users = []
        total = 0
        page = 1
        per_page = 20
        total_pages = 0
        search = ""
    return templates.TemplateResponse("users.html", {
        "request": request,
        "page": "users",
        "users": users,
        "total": total,
        "current_page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "search": search,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        user_res = sb.table("users").select("*").eq("id", user_id).execute()
        user = user_res.data[0] if user_res.data else None
        if not user:
            return RedirectResponse(url="/panel/users?error=Usuario+no+encontrado", status_code=302)
        subs_res = sb.table("subscriptions").select(
            "*, platforms(name, icon_emoji), profiles(profile_name, pin)"
        ).eq("user_id", user_id).order("created_at", desc=True).execute()
        subscriptions = subs_res.data or []
    except Exception as e:
        logger.error(f"User detail error: {e}")
        return RedirectResponse(url=f"/panel/users?error={str(e)[:100]}", status_code=302)
    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "page": "users",
        "user": user,
        "subscriptions": subscriptions,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/users/{user_id}/block")
async def user_block(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("users").update({"status": "blocked"}).eq("id", user_id).execute()
        return RedirectResponse(url=f"/panel/users/{user_id}?success=Usuario+bloqueado", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/users/{user_id}?error={str(e)[:100]}", status_code=302)


@panel_router.post("/users/{user_id}/unblock")
async def user_unblock(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("users").update({"status": "active"}).eq("id", user_id).execute()
        return RedirectResponse(url=f"/panel/users/{user_id}?success=Usuario+desbloqueado", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/users/{user_id}?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# SUBSCRIPTIONS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from database.platforms import get_active_platforms
        sb = get_supabase()

        status_filter = request.query_params.get("status", "")
        platform_filter = request.query_params.get("platform", "")
        plan_filter = request.query_params.get("plan", "")
        page = int(request.query_params.get("page", 1))
        per_page = 25
        offset = (page - 1) * per_page

        query = sb.table("subscriptions").select(
            "*, users(name, username), platforms(name, icon_emoji, slug)"
        )
        if status_filter:
            query = query.eq("status", status_filter)
        if platform_filter:
            query = query.eq("platform_id", platform_filter)
        if plan_filter:
            query = query.eq("plan_type", plan_filter)

        result = query.order("created_at", desc=True).range(offset, offset + per_page - 1).execute()
        subscriptions = result.data or []

        count_q = sb.table("subscriptions").select("id", count="exact")
        if status_filter:
            count_q = count_q.eq("status", status_filter)
        if platform_filter:
            count_q = count_q.eq("platform_id", platform_filter)
        if plan_filter:
            count_q = count_q.eq("plan_type", plan_filter)
        count_res = count_q.execute()
        total = count_res.count or 0
        total_pages = (total + per_page - 1) // per_page

        platforms = await get_active_platforms()
    except Exception as e:
        logger.error(f"Subscriptions list error: {e}")
        subscriptions = []
        platforms = []
        total = 0
        total_pages = 0
        page = 1
        per_page = 25
    return templates.TemplateResponse("subscriptions.html", {
        "request": request,
        "page": "subscriptions",
        "subscriptions": subscriptions,
        "platforms": platforms,
        "status_filter": request.query_params.get("status", ""),
        "platform_filter": request.query_params.get("platform", ""),
        "plan_filter": request.query_params.get("plan", ""),
        "total": total,
        "current_page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/payments", response_class=HTMLResponse)
async def payments_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.subscriptions import get_pending_subscriptions
        from database import get_supabase
        from database.platforms import get_active_platforms
        pending = await get_pending_subscriptions()
        sb = get_supabase()
        # Get available profiles per platform for assignment
        profiles_map: dict = {}
        for sub in pending:
            pid = sub.get("platform_id")
            if pid and pid not in profiles_map:
                pres = sb.table("profiles").select("id, profile_name, profile_type").eq(
                    "platform_id", pid
                ).eq("status", "available").execute()
                profiles_map[pid] = pres.data or []
    except Exception as e:
        logger.error(f"Payments list error: {e}")
        pending = []
        profiles_map = {}
    return templates.TemplateResponse("payments.html", {
        "request": request,
        "page": "payments",
        "pending": pending,
        "profiles_map": profiles_map,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/payments/{sub_id}/approve")
async def payment_approve(
    request: Request,
    sub_id: str,
    profile_id: str = Form(...),
    payment_reference: str = Form(default="PANEL-MANUAL"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.subscriptions import confirm_subscription, get_subscription_by_id
        from database.profiles import assign_profile
        sub = await get_subscription_by_id(sub_id)
        if not sub:
            return RedirectResponse(url="/panel/payments?error=Suscripcion+no+encontrada", status_code=302)
        image_url = sub.get("payment_image_url") or ""
        ok = await confirm_subscription(sub_id, profile_id, payment_reference, image_url)
        if ok:
            await assign_profile(profile_id)
            # Notify user via Telegram
            try:
                user = sub.get("users") or {}
                platform = sub.get("platforms") or {}
                telegram_id = user.get("telegram_id")
                if telegram_id:
                    from database import get_supabase
                    sb = get_supabase()
                    profile_res = sb.table("profiles").select("profile_name, pin").eq("id", profile_id).execute()
                    profile_data = profile_res.data[0] if profile_res.data else {}
                    from services.notification_service import send_to_user
                    msg = (
                        f"✅ <b>¡Pago aprobado!</b>\n\n"
                        f"Tu suscripción de <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b> ha sido activada.\n\n"
                        f"📱 <b>Perfil:</b> {profile_data.get('profile_name','')}\n"
                        f"🔑 <b>PIN:</b> {profile_data.get('pin') or 'Sin PIN'}\n\n"
                        f"¡Disfruta tu servicio!"
                    )
                    await send_to_user(telegram_id, msg)
            except Exception as notify_err:
                logger.warning(f"Could not notify user after approve: {notify_err}")
            return RedirectResponse(url="/panel/payments?success=Pago+aprobado+y+perfil+asignado", status_code=302)
        return RedirectResponse(url="/panel/payments?error=Error+al+confirmar+pago", status_code=302)
    except Exception as e:
        logger.error(f"Payment approve error: {e}")
        return RedirectResponse(url=f"/panel/payments?error={str(e)[:100]}", status_code=302)


@panel_router.post("/payments/{sub_id}/reject")
async def payment_reject(
    request: Request,
    sub_id: str,
    reason: str = Form(default="Pago rechazado por el administrador"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from database.subscriptions import get_subscription_by_id
        sb = get_supabase()
        sub = await get_subscription_by_id(sub_id)
        sb.table("subscriptions").update({"status": "cancelled"}).eq("id", sub_id).execute()
        # Notify user
        try:
            if sub:
                user = sub.get("users") or {}
                platform = sub.get("platforms") or {}
                telegram_id = user.get("telegram_id")
                if telegram_id:
                    from services.notification_service import send_to_user
                    msg = (
                        f"❌ <b>Pago rechazado</b>\n\n"
                        f"Tu pago para <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b> fue rechazado.\n\n"
                        f"Motivo: {reason}\n\n"
                        f"Contáctanos si crees que es un error."
                    )
                    await send_to_user(telegram_id, msg)
        except Exception as notify_err:
            logger.warning(f"Could not notify user after reject: {notify_err}")
        return RedirectResponse(url="/panel/payments?success=Pago+rechazado", status_code=302)
    except Exception as e:
        logger.error(f"Payment reject error: {e}")
        return RedirectResponse(url=f"/panel/payments?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# PLATFORMS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/platforms", response_class=HTMLResponse)
async def platforms_list(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        result = sb.table("platforms").select("*").order("name").execute()
        platforms = result.data or []
    except Exception as e:
        logger.error(f"Platforms list error: {e}")
        platforms = []
    return templates.TemplateResponse("platforms.html", {
        "request": request,
        "page": "platforms",
        "platforms": platforms,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.get("/platforms/{platform_id}/edit", response_class=HTMLResponse)
async def platform_edit_form(request: Request, platform_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.platforms import get_platform_by_id
        platform = await get_platform_by_id(platform_id)
        if not platform:
            return RedirectResponse(url="/panel/platforms?error=Plataforma+no+encontrada", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/platforms?error={str(e)[:100]}", status_code=302)
    return templates.TemplateResponse("platform_form.html", {
        "request": request,
        "page": "platforms",
        "platform": platform,
    })


@panel_router.post("/platforms/{platform_id}/edit")
async def platform_edit_save(
    request: Request,
    platform_id: str,
    name: str = Form(...),
    slug: str = Form(...),
    icon_emoji: str = Form(default=""),
    monthly_price_usd: float = Form(default=0.0),
    express_price_usd: float = Form(default=0.0),
    week_price_usd: float = Form(default=0.0),
    is_active: str = Form(default="off"),
    instructions_monthly: str = Form(default=""),
    instructions_express: str = Form(default=""),
    instructions_week: str = Form(default=""),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        sb.table("platforms").update({
            "name": name,
            "slug": slug,
            "icon_emoji": icon_emoji,
            "monthly_price_usd": monthly_price_usd,
            "express_price_usd": express_price_usd,
            "week_price_usd": week_price_usd,
            "is_active": is_active == "on",
            "instructions_monthly": instructions_monthly or None,
            "instructions_express": instructions_express or None,
            "instructions_week": instructions_week or None,
        }).eq("id", platform_id).execute()
        return RedirectResponse(url="/panel/platforms?success=Plataforma+actualizada", status_code=302)
    except Exception as e:
        logger.error(f"Platform edit error: {e}")
        return RedirectResponse(url=f"/panel/platforms?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from services.exchange_service import get_current_rate
        from database import get_supabase
        rate_data = await get_current_rate()
        sb = get_supabase()
        config_res = sb.table("payment_config").select("*").limit(1).execute()
        payment_config = config_res.data[0] if config_res.data else {}
    except Exception as e:
        logger.error(f"Settings page error: {e}")
        rate_data = None
        payment_config = {}
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "page": "settings",
        "rate_data": rate_data,
        "payment_config": payment_config,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/settings/rate")
async def settings_update_rate(
    request: Request,
    usd_binance: float = Form(...),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from services.exchange_service import update_rate
        await update_rate(usd_binance, 0)
        return RedirectResponse(url="/panel/settings?success=Tasa+Binance+actualizada", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/settings?error={str(e)[:100]}", status_code=302)


@panel_router.post("/settings/bcv")
async def settings_update_bcv(
    request: Request,
    usd_bcv: float = Form(...),
    eur_bcv: float = Form(default=0.0),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from services.exchange_service import get_current_rate, update_rate
        current = await get_current_rate()
        current_binance = float(current.get("usd_binance", 0)) if current else 0.0
        await update_rate(current_binance or usd_bcv, 0, usd_bcv=usd_bcv, eur_bcv=eur_bcv if eur_bcv > 0 else None)
        return RedirectResponse(url="/panel/settings?success=Tasas+BCV+actualizadas", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/settings?error={str(e)[:100]}", status_code=302)


@panel_router.post("/settings/payment")
async def settings_update_payment(
    request: Request,
    banco: str = Form(default=""),
    telefono: str = Form(default=""),
    cedula: str = Form(default=""),
    titular: str = Form(default=""),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        existing = sb.table("payment_config").select("id").limit(1).execute()
        data = {"banco": banco, "telefono": telefono, "cedula": cedula, "titular": titular}
        if existing.data:
            sb.table("payment_config").update(data).eq("id", existing.data[0]["id"]).execute()
        else:
            sb.table("payment_config").insert(data).execute()
        return RedirectResponse(url="/panel/settings?success=Configuracion+de+pago+actualizada", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/settings?error={str(e)[:100]}", status_code=302)


# ─────────────────────────────────────────────────────────────────────────────
# MIGRACIÓN DE CLIENTES
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/migrate", response_class=HTMLResponse)
async def migrate_get(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.platforms import get_active_platforms
        from database import get_supabase
        sb = get_supabase()
        platforms = await get_active_platforms()
        profiles_res = sb.table("profiles").select(
            "id, profile_name, profile_type, platform_id, platforms(name, icon_emoji)"
        ).eq("status", "available").order("profile_type").execute()
        available_profiles = profiles_res.data or []
    except Exception as e:
        logger.error(f"Migrate page error: {e}")
        platforms = []
        available_profiles = []
    return templates.TemplateResponse("migrate.html", {
        "request": request,
        "page": "migrate",
        "platforms": platforms,
        "available_profiles": available_profiles,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/migrate")
async def migrate_post(request: Request):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from utils.helpers import venezuela_now
        from datetime import datetime
        import pytz
        from typing import List

        form = await request.form()
        sb = get_supabase()
        tz_ve = pytz.timezone("America/Caracas")

        telegram_id   = int(form.get("telegram_id", 0))
        client_name   = form.get("client_name", "").strip()
        username      = form.get("username", "").strip()
        notes         = form.get("notes", "").strip()

        # Multi-value fields (one per subscription row)
        platform_ids  = form.getlist("platform_id")
        profile_ids   = form.getlist("profile_id")
        plan_types    = form.getlist("plan_type")
        end_dates     = form.getlist("end_date")
        prices        = form.getlist("price_usd")
        references    = form.getlist("payment_reference")

        if not telegram_id or not client_name or not platform_ids:
            return RedirectResponse(url="/panel/migrate?error=Faltan+campos+obligatorios", status_code=302)

        # 1. Upsert user
        existing_user = sb.table("users").select("id, total_purchases").eq("telegram_id", telegram_id).limit(1).execute()
        if existing_user.data:
            user_id = existing_user.data[0]["id"]
            current_purchases = existing_user.data[0].get("total_purchases", 0) or 0
            upd = {"name": client_name, "last_seen": venezuela_now().isoformat()}
            if username: upd["username"] = username
            if notes: upd["notes"] = notes
            sb.table("users").update(upd).eq("id", user_id).execute()
        else:
            ins = {"telegram_id": telegram_id, "name": client_name, "status": "active"}
            if username: ins["username"] = username
            if notes: ins["notes"] = notes
            new_user = sb.table("users").insert(ins).execute()
            user_id = new_user.data[0]["id"]
            current_purchases = 0

        # 2. Create one subscription per row
        created = 0
        for i, pid in enumerate(platform_ids):
            prof_id  = profile_ids[i] if i < len(profile_ids) else ""
            plan     = plan_types[i]  if i < len(plan_types)  else "monthly"
            ed_str   = end_dates[i]   if i < len(end_dates)   else ""
            price    = float(prices[i]) if i < len(prices) and prices[i] else 0.0
            ref      = references[i]  if i < len(references)  else "MIGRADO"

            if not pid or not prof_id or not ed_str:
                continue

            end_dt = datetime.strptime(ed_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            end_dt = tz_ve.localize(end_dt)

            sb.table("subscriptions").insert({
                "user_id": user_id,
                "profile_id": prof_id,
                "platform_id": pid,
                "plan_type": plan,
                "start_date": venezuela_now().isoformat(),
                "end_date": end_dt.isoformat(),
                "price_usd": price,
                "status": "active",
                "payment_reference": ref or "MIGRADO",
                "payment_confirmed_at": venezuela_now().isoformat(),
                "reminder_sent": False,
                "expiry_notified": False,
            }).execute()

            sb.table("profiles").update({"status": "occupied"}).eq("id", prof_id).execute()
            created += 1

        # 3. Update total_purchases
        sb.table("users").update({"total_purchases": current_purchases + created}).eq("id", user_id).execute()

        logger.info(f"Migrated {client_name} (TG:{telegram_id}) — {created} subscription(s)")
        return RedirectResponse(
            url=f"/panel/migrate?success={client_name}+migrado+con+{created}+suscripcion(es)",
            status_code=302,
        )
    except Exception as e:
        logger.error(f"Migrate error: {e}")
        return RedirectResponse(url=f"/panel/migrate?error={str(e)[:120]}", status_code=302)


@panel_router.get("/api/profiles/available/{platform_id}")
async def api_profiles_by_platform(request: Request, platform_id: str):
    """Return available profiles for a given platform (used by migrate form JS)."""
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database import get_supabase
        sb = get_supabase()
        res = sb.table("profiles").select("id, profile_name, profile_type").eq(
            "platform_id", platform_id
        ).eq("status", "available").order("profile_type").execute()
        return JSONResponse({"profiles": res.data or []})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ─────────────────────────────────────────────────────────────────────────────
# API / JSON ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@panel_router.get("/api/stats")
async def api_stats(request: Request):
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database.analytics import get_dashboard_stats
        stats = await get_dashboard_stats()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@panel_router.get("/api/rate/fetch")
async def api_fetch_rate(request: Request):
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from services.exchange_service import fetch_binance_p2p_rate, update_rate
        rate = await fetch_binance_p2p_rate()
        if rate:
            await update_rate(rate, 0)
            return JSONResponse({"success": True, "rate": rate, "message": f"Tasa actualizada: Bs {rate:.2f}/USD"})
        return JSONResponse({"success": False, "message": "No se pudo obtener la tasa de Binance P2P"})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@panel_router.get("/api/payments/pending")
async def api_payments_pending(request: Request):
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database.subscriptions import get_pending_subscriptions
        pending = await get_pending_subscriptions()
        return JSONResponse({"count": len(pending), "payments": pending})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
