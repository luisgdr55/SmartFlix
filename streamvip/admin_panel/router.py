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

        # Expiring subscriptions (clients) within 3 days
        import pytz
        tz_ve = pytz.timezone("America/Caracas")
        now_ve = venezuela_now()
        today_str = now_ve.strftime("%Y-%m-%d")
        in_3_days_str = (now_ve + timedelta(days=3)).strftime("%Y-%m-%d")
        in_3_days = now_ve + timedelta(days=3)
        expiring_alert = sb.table("subscriptions").select(
            "id, end_date, plan_type, users(name, username), platforms(name, icon_emoji)"
        ).eq("status", "active").gte("end_date", now_ve.isoformat()).lte(
            "end_date", in_3_days.isoformat()
        ).order("end_date").execute()

        # Accounts with billing_date today or overdue (past due or within 3 days)
        accounts_due = sb.table("accounts").select(
            "id, email, billing_date, platforms(name, icon_emoji)"
        ).eq("status", "active").not_.is_("billing_date", "null").lte(
            "billing_date", in_3_days_str
        ).order("billing_date").execute()

        # Client subscriptions already expired (status=active but end_date in the past)
        expired_subs = sb.table("subscriptions").select(
            "id, end_date, plan_type, profile_id, user_id, users(id, name, username), platforms(name, icon_emoji)"
        ).eq("status", "active").lt(
            "end_date", now_ve.isoformat()
        ).order("end_date").limit(30).execute()

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
            "expiring_alert": expiring_alert.data or [],
            "accounts_due": accounts_due.data or [],
            "expired_subs": expired_subs.data or [],
            "today_str": today_str,
            "now_ve": now_ve,
        }
    except Exception as e:
        logger.error(f"Dashboard error: {e}")
        from utils.helpers import venezuela_now as _ve_now
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
            "expiring_alert": [],
            "accounts_due": [],
            "expired_subs": [],
            "today_str": _ve_now().strftime("%Y-%m-%d"),
            "now_ve": _ve_now(),
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
    is_extra_member: str = Form(default="off"),
    extra_email: str = Form(default=""),
    extra_password: str = Form(default=""),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        extra = is_extra_member == "on"
        sb.table("profiles").insert({
            "account_id": account_id,
            "platform_id": platform_id,
            "profile_name": profile_name,
            "pin": pin or None,
            "profile_type": profile_type,
            "status": status,
            "is_extra_member": extra,
            "extra_email": extra_email or None,
            "extra_password": extra_password or None,
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
    is_extra_member: str = Form(default="off"),
    extra_email: str = Form(default=""),
    extra_password: str = Form(default=""),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()
        extra = is_extra_member == "on"
        sb.table("profiles").update({
            "account_id": account_id,
            "platform_id": platform_id,
            "profile_name": profile_name,
            "pin": pin or None,
            "profile_type": profile_type,
            "status": status,
            "is_extra_member": extra,
            "extra_email": extra_email or None,
            "extra_password": extra_password or None,
        }).eq("id", profile_id).execute()
        return RedirectResponse(url="/panel/profiles?success=Perfil+actualizado", status_code=302)
    except Exception as e:
        logger.error(f"Profile edit error: {e}")
        return RedirectResponse(url=f"/panel/profiles?error={str(e)[:100]}", status_code=302)


@panel_router.post("/profiles/{profile_id}/set-status")
async def profile_set_status(request: Request, profile_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        form = await request.form()
        status = (form.get("status") or "").strip()
        if status not in ("available", "occupied", "maintenance"):
            return RedirectResponse(url="/panel/profiles?error=Estado+invalido", status_code=302)
        sb = get_supabase()
        sb.table("profiles").update({"status": status}).eq("id", profile_id).execute()
        return RedirectResponse(url=request.headers.get("referer", "/panel/profiles"), status_code=302)
    except Exception as e:
        logger.error(f"Profile set-status error: {e}")
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
        from database.platforms import get_active_platforms
        sb = get_supabase()
        user_res = sb.table("users").select("*").eq("id", user_id).execute()
        user = user_res.data[0] if user_res.data else None
        if not user:
            return RedirectResponse(url="/panel/users?error=Usuario+no+encontrado", status_code=302)
        subs_res = sb.table("subscriptions").select(
            "*, platforms(name, icon_emoji), profiles(profile_name, pin)"
        ).eq("user_id", user_id).order("created_at", desc=True).execute()
        subscriptions = subs_res.data or []
        platforms = await get_active_platforms()
    except Exception as e:
        logger.error(f"User detail error: {e}")
        return RedirectResponse(url=f"/panel/users?error={str(e)[:100]}", status_code=302)
    from utils.helpers import venezuela_now
    return templates.TemplateResponse("user_detail.html", {
        "request": request,
        "page": "users",
        "user": user,
        "subscriptions": subscriptions,
        "platforms": platforms,
        "now_date": venezuela_now().strftime("%Y-%m-%d"),
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/users/{user_id}/add-subscription")
async def user_add_subscription(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        from utils.helpers import venezuela_now
        from datetime import datetime
        import pytz

        form = await request.form()
        sb = get_supabase()
        tz_ve = pytz.timezone("America/Caracas")

        platform_id  = (form.get("platform_id") or "").strip()
        plan_type    = (form.get("plan_type") or "monthly").strip()
        end_date_str = (form.get("end_date") or "").strip()
        price        = float(form.get("price_usd") or 0)
        reference    = (form.get("payment_reference") or "MIGRADO").strip()
        profile_mode = (form.get("profile_mode") or "existing").strip()

        if not platform_id or not end_date_str:
            return RedirectResponse(url=f"/panel/users/{user_id}?error=Plataforma+y+fecha+son+obligatorios", status_code=302)

        if profile_mode == "new":
            acc_id   = (form.get("new_account_id") or "").strip()
            pname    = (form.get("new_profile_name") or "").strip()
            ptype    = (form.get("new_profile_type") or "monthly").strip()
            pin_val  = (form.get("new_pin") or "").strip()
            is_extra = form.get("new_is_extra_member") == "on"
            ex_email = (form.get("new_extra_email") or "").strip()
            ex_pass  = (form.get("new_extra_password") or "").strip()
            if not acc_id or not pname:
                return RedirectResponse(url=f"/panel/users/{user_id}?error=Cuenta+y+nombre+de+perfil+son+obligatorios", status_code=302)
            profile_ins: dict = {
                "platform_id": platform_id,
                "account_id": acc_id,
                "profile_name": pname,
                "profile_type": ptype,
                "status": "occupied",
                "is_extra_member": is_extra,
            }
            if is_extra:
                if ex_email: profile_ins["extra_email"] = ex_email
                if ex_pass:  profile_ins["extra_password"] = ex_pass
            else:
                if pin_val:  profile_ins["pin"] = pin_val
            new_prof = sb.table("profiles").insert(profile_ins).execute()
            prof_id  = new_prof.data[0]["id"]
        else:
            prof_id = (form.get("profile_id") or "").strip()
            if not prof_id:
                return RedirectResponse(url=f"/panel/users/{user_id}?error=Debes+seleccionar+un+perfil", status_code=302)
            sb.table("profiles").update({"status": "occupied"}).eq("id", prof_id).execute()

        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        end_dt = tz_ve.localize(end_dt)

        new_sub = sb.table("subscriptions").insert({
            "user_id": user_id,
            "profile_id": prof_id,
            "platform_id": platform_id,
            "plan_type": plan_type,
            "start_date": venezuela_now().isoformat(),
            "end_date": end_dt.isoformat(),
            "price_usd": price,
            "status": "active",
            "payment_reference": reference or "MIGRADO",
            "payment_confirmed_at": venezuela_now().isoformat(),
            "reminder_sent": False,
            "expiry_notified": False,
        }).execute()

        # Update total_purchases
        user_res = sb.table("users").select("total_purchases").eq("id", user_id).execute()
        current = (user_res.data[0].get("total_purchases") or 0) if user_res.data else 0
        sb.table("users").update({"total_purchases": current + 1}).eq("id", user_id).execute()

        # Send reminder if expiring soon
        days_left = (end_dt.astimezone(tz_ve) - venezuela_now()).days
        sub_id = new_sub.data[0]["id"] if new_sub.data else None
        if sub_id and days_left <= 3:
            try:
                from services.notification_service import send_expiry_reminder
                await send_expiry_reminder(str(sub_id))
            except Exception as notify_err:
                logger.warning(f"Could not send immediate reminder: {notify_err}")

        return RedirectResponse(url=f"/panel/users/{user_id}?success=Suscripcion+agregada+correctamente", status_code=302)
    except Exception as e:
        logger.error(f"Add subscription error: {e}")
        return RedirectResponse(url=f"/panel/users/{user_id}?error={str(e)[:120]}", status_code=302)


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


@panel_router.post("/users/{user_id}/edit")
async def user_edit(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        form = await request.form()
        sb = get_supabase()
        upd: dict = {}
        name = (form.get("name") or "").strip()
        phone = (form.get("phone") or "").strip()
        username = (form.get("username") or "").strip().lstrip("@")
        telegram_id_raw = (form.get("telegram_id") or "").strip()
        if name:
            upd["name"] = name
        if phone:
            upd["phone"] = phone
        if username:
            upd["username"] = username
        if telegram_id_raw:
            try:
                upd["telegram_id"] = int(telegram_id_raw)
            except ValueError:
                return RedirectResponse(url=f"/panel/users/{user_id}?error=Telegram+ID+debe+ser+un+numero", status_code=302)
        if upd:
            sb.table("users").update(upd).eq("id", user_id).execute()
        return RedirectResponse(url=f"/panel/users/{user_id}?success=Cliente+actualizado+correctamente", status_code=302)
    except Exception as e:
        logger.error(f"User edit error: {e}")
        return RedirectResponse(url=f"/panel/users/{user_id}?error={str(e)[:100]}", status_code=302)


@panel_router.post("/users/{user_id}/delete")
async def user_delete(request: Request, user_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.users import delete_user
        await delete_user(user_id)
        return RedirectResponse(url="/panel/users?success=Cliente+eliminado+correctamente", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/users?error={str(e)[:100]}", status_code=302)


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


@panel_router.post("/subscriptions/{sub_id}/edit")
async def subscription_edit(request: Request, sub_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        form = await request.form()
        sb = get_supabase()

        # Fetch sub to get user_id for redirect
        sub_res = sb.table("subscriptions").select("user_id").eq("id", sub_id).limit(1).execute()
        user_id = sub_res.data[0]["user_id"] if sub_res.data else None

        upd: dict = {}
        end_date = (form.get("end_date") or "").strip()
        status = (form.get("status") or "").strip()
        plan_type = (form.get("plan_type") or "").strip()
        price_usd = (form.get("price_usd") or "").strip()

        if end_date:
            # Store as end-of-day in ISO format
            from datetime import datetime
            import pytz
            tz_ve = pytz.timezone("America/Caracas")
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            upd["end_date"] = tz_ve.localize(dt).isoformat()
        if status:
            upd["status"] = status
        if plan_type:
            upd["plan_type"] = plan_type
        if price_usd:
            try:
                upd["price_usd"] = float(price_usd)
            except ValueError:
                pass

        if upd:
            sb.table("subscriptions").update(upd).eq("id", sub_id).execute()

        redirect_url = f"/panel/users/{user_id}?success=Suscripcion+actualizada" if user_id else "/panel/subscriptions?success=Suscripcion+actualizada"
        return RedirectResponse(url=redirect_url, status_code=302)
    except Exception as e:
        logger.error(f"Subscription edit error: {e}")
        return RedirectResponse(url=f"/panel/subscriptions?error={str(e)[:100]}", status_code=302)


@panel_router.post("/subscriptions/{sub_id}/soft-cut")
async def subscription_soft_cut(request: Request, sub_id: str):
    """Change profile PIN (soft cut) and mark subscription expired."""
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        import random
        import string
        from database import get_supabase
        sb = get_supabase()

        # Fetch subscription with profile_id
        sub_res = sb.table("subscriptions").select(
            "user_id, profile_id, plan_type, platforms(name, icon_emoji)"
        ).eq("id", sub_id).limit(1).execute()
        if not sub_res.data:
            return RedirectResponse(url="/panel/?error=Suscripcion+no+encontrada", status_code=302)
        sub = sub_res.data[0]
        user_id = sub.get("user_id")
        profile_id = sub.get("profile_id")

        if not profile_id:
            return RedirectResponse(url=f"/panel/users/{user_id}?error=Esta+suscripcion+no+tiene+perfil+asignado", status_code=302)

        # Generate random 4-digit PIN
        new_pin = "".join(random.choices(string.digits, k=4))
        sb.table("profiles").update({"pin": new_pin}).eq("id", profile_id).execute()

        # Mark subscription as expired
        sb.table("subscriptions").update({"status": "expired"}).eq("id", sub_id).execute()

        # Notify user via Telegram (best-effort)
        try:
            from services.notification_service import send_soft_cut_notification
            await send_soft_cut_notification(sub_id)
        except Exception as notify_err:
            logger.warning(f"Could not send soft-cut notification: {notify_err}")

        redirect_base = f"/panel/users/{user_id}" if user_id else "/panel/"
        return RedirectResponse(url=f"{redirect_base}?success=Corte+suave+aplicado+y+PIN+cambiado", status_code=302)
    except Exception as e:
        logger.error(f"Soft cut error: {e}")
        return RedirectResponse(url=f"/panel/?error={str(e)[:120]}", status_code=302)


@panel_router.post("/subscriptions/{sub_id}/release")
async def subscription_release(request: Request, sub_id: str):
    """Release profile (mark available) and mark subscription expired."""
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database import get_supabase
        sb = get_supabase()

        sub_res = sb.table("subscriptions").select(
            "user_id, profile_id"
        ).eq("id", sub_id).limit(1).execute()
        if not sub_res.data:
            return RedirectResponse(url="/panel/?error=Suscripcion+no+encontrada", status_code=302)
        sub = sub_res.data[0]
        user_id = sub.get("user_id")
        profile_id = sub.get("profile_id")

        if profile_id:
            from utils.helpers import venezuela_now
            sb.table("profiles").update({
                "status": "available",
                "last_released": venezuela_now().isoformat(),
            }).eq("id", profile_id).execute()

        # Mark subscription as expired
        sb.table("subscriptions").update({"status": "expired"}).eq("id", sub_id).execute()

        # Notify user via Telegram (best-effort)
        try:
            from services.notification_service import send_profile_released_notification
            await send_profile_released_notification(sub_id)
        except Exception as notify_err:
            logger.warning(f"Could not send release notification: {notify_err}")

        redirect_base = f"/panel/users/{user_id}" if user_id else "/panel/"
        return RedirectResponse(url=f"{redirect_base}?success=Perfil+liberado+correctamente", status_code=302)
    except Exception as e:
        logger.error(f"Release subscription error: {e}")
        return RedirectResponse(url=f"/panel/?error={str(e)[:120]}", status_code=302)


@panel_router.post("/subscriptions/{sub_id}/delete")
async def subscription_delete(request: Request, sub_id: str):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.subscriptions import delete_subscription
        await delete_subscription(sub_id)
        return RedirectResponse(url="/panel/subscriptions?success=Suscripcion+eliminada+correctamente", status_code=302)
    except Exception as e:
        return RedirectResponse(url=f"/panel/subscriptions?error={str(e)[:100]}", status_code=302)


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
        from utils.helpers import venezuela_now
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
        # Expired subscriptions (status=active but end_date in the past)
        now = venezuela_now()
        expired_res = (
            sb.table("subscriptions")
            .select("*, users(id, name, username, telegram_id), platforms(name, icon_emoji)")
            .eq("status", "active")
            .lt("end_date", now.isoformat())
            .order("end_date", desc=False)
            .limit(50)
            .execute()
        )
        expired = expired_res.data or []
        # Detect renewals: check if each pending sub's user already has a profile for that platform
        from database.subscriptions import get_user_platform_active_subscription
        renewal_map: dict = {}
        for sub in pending:
            uid = str(sub.get("user_id", ""))
            pid = str(sub.get("platform_id", ""))
            if uid and pid:
                existing = await get_user_platform_active_subscription(uid, pid)
                renewal_map[sub["id"]] = bool(existing and existing.get("profile_id"))
    except Exception as e:
        logger.error(f"Payments list error: {e}")
        pending = []
        profiles_map = {}
        expired = []
        renewal_map = {}
    return templates.TemplateResponse("payments.html", {
        "request": request,
        "page": "payments",
        "pending": pending,
        "profiles_map": profiles_map,
        "renewal_map": renewal_map,
        "expired": expired,
        "success": request.query_params.get("success"),
        "error": request.query_params.get("error"),
    })


@panel_router.post("/payments/{sub_id}/approve")
async def payment_approve(
    request: Request,
    sub_id: str,
    profile_id: str = Form(default=""),
    payment_reference: str = Form(default="PANEL-MANUAL"),
):
    guard = _auth_guard(request)
    if guard:
        return guard
    try:
        from database.subscriptions import (
            confirm_subscription, get_subscription_by_id,
            get_user_platform_active_subscription, confirm_renewal_subscription,
        )
        from database.profiles import assign_profile
        from database.users import increment_user_purchases
        from services.notification_service import send_to_user
        from utils.helpers import venezuela_now, format_datetime_vzla
        from datetime import datetime, timedelta

        sub = await get_subscription_by_id(sub_id)
        if not sub:
            return RedirectResponse(url="/panel/payments?error=Suscripcion+no+encontrada", status_code=302)

        user_id = str(sub.get("user_id", ""))
        platform_id = str(sub.get("platform_id", ""))
        plan_type = sub.get("plan_type", "monthly")
        user = sub.get("users") or {}
        platform = sub.get("platforms") or {}
        telegram_id = user.get("telegram_id")
        platform_label = f"{platform.get('icon_emoji','')} {platform.get('name','')}"
        durations = {"monthly": 30, "express": 1, "week": 7}
        duration_days = durations.get(plan_type, 30)

        # ── RENEWAL CHECK ──────────────────────────────────────────────
        existing_sub = await get_user_platform_active_subscription(user_id, platform_id)
        if existing_sub and existing_sub.get("profile_id"):
            existing_profile_id = str(existing_sub["profile_id"])
            profile_data = existing_sub.get("profiles") or {}

            now = venezuela_now()
            existing_end_str = (existing_sub.get("end_date") or "")[:10]
            today_str = now.strftime("%Y-%m-%d")
            if existing_end_str > today_str:
                try:
                    base = datetime.fromisoformat(existing_sub["end_date"].replace("Z", "+00:00"))
                    base = base.replace(tzinfo=now.tzinfo)
                except Exception:
                    base = now
            else:
                base = now
            new_end_date = base + timedelta(days=duration_days)

            ok = await confirm_renewal_subscription(sub_id, existing_profile_id, payment_reference, new_end_date)
            if ok:
                if telegram_id:
                    await increment_user_purchases(telegram_id)
                    try:
                        renewal_msg = (
                            f"✅ <b>¡Renovación confirmada!</b>\n\n"
                            f"Tu suscripción de <b>{platform_label}</b> ha sido renovada.\n\n"
                            f"👤 Perfil: <b>{profile_data.get('profile_name', '')}</b>\n"
                            f"📅 Nueva fecha de corte: <b>{format_datetime_vzla(new_end_date)}</b>\n\n"
                            f"¡Gracias por tu preferencia! 🙌 Disfruta el streaming. 🎬"
                        )
                        await send_to_user(telegram_id, renewal_msg)
                    except Exception as notify_err:
                        logger.warning(f"Could not notify renewal: {notify_err}")
                return RedirectResponse(url="/panel/payments?success=Renovacion+aprobada+y+cliente+notificado", status_code=302)
            return RedirectResponse(url="/panel/payments?error=Error+al+confirmar+renovacion", status_code=302)

        # ── NEW SUBSCRIPTION PATH ──────────────────────────────────────
        if not profile_id:
            return RedirectResponse(url="/panel/payments?error=Selecciona+un+perfil+para+nueva+suscripcion", status_code=302)

        image_url = sub.get("payment_image_url") or ""
        ok = await confirm_subscription(sub_id, profile_id, payment_reference, image_url)
        if ok:
            await assign_profile(profile_id)
            if telegram_id:
                await increment_user_purchases(telegram_id)
            try:
                from database import get_supabase
                sb = get_supabase()
                profile_res = sb.table("profiles").select("profile_name, pin").eq("id", profile_id).execute()
                profile_data = profile_res.data[0] if profile_res.data else {}
                msg = (
                    f"✅ <b>¡Pago aprobado!</b>\n\n"
                    f"Tu suscripción de <b>{platform_label}</b> ha sido activada.\n\n"
                    f"📱 <b>Perfil:</b> {profile_data.get('profile_name','')}\n"
                    f"🔑 <b>PIN:</b> {profile_data.get('pin') or 'Sin PIN'}\n\n"
                    f"¡Disfruta tu servicio! 🎬"
                )
                if telegram_id:
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
        from database.subscriptions import get_subscription_by_id, delete_subscription, get_user_active_subscriptions
        sub = await get_subscription_by_id(sub_id)
        await delete_subscription(sub_id)

        if sub:
            # Delete user if they have no other subscriptions (no failed attempts accumulate)
            user_id = sub.get("user_id")
            if user_id:
                try:
                    remaining = await get_user_active_subscriptions(str(user_id))
                    if not remaining:
                        from database.users import delete_user
                        await delete_user(str(user_id))
                        logger.info(f"Deleted user {user_id} after web panel payment rejection")
                except Exception as del_err:
                    logger.warning(f"Could not delete user after reject: {del_err}")

            # Notify user via Telegram
            try:
                user = sub.get("users") or {}
                platform = sub.get("platforms") or {}
                telegram_id = user.get("telegram_id")
                if telegram_id:
                    from services.notification_service import send_to_user
                    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
                    restart_kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔄 Iniciar nuevo pedido", callback_data="menu:subscribe")
                    ]])
                    msg = (
                        f"❌ <b>Comprobante no aprobado</b>\n\n"
                        f"Tu pago para <b>{platform.get('icon_emoji','')} {platform.get('name','')}</b> "
                        f"no pudo ser verificado.\n\n"
                        f"Motivo: {reason}\n\n"
                        f"Inicia un nuevo pedido y envía el comprobante correcto."
                    )
                    await send_to_user(telegram_id, msg, keyboard=restart_kb)
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
        platforms = await get_active_platforms()
    except Exception as e:
        logger.error(f"Migrate page error: {e}")
        platforms = []
    return templates.TemplateResponse("migrate.html", {
        "request": request,
        "page": "migrate",
        "platforms": platforms,
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

        telegram_id_raw = form.get("telegram_id", "").strip()
        telegram_id   = int(telegram_id_raw) if telegram_id_raw else None
        client_name   = form.get("client_name", "").strip()
        username      = form.get("username", "").strip().lstrip("@")
        phone         = form.get("phone", "").strip()
        notes         = form.get("notes", "").strip()

        # Multi-value fields (one per subscription row)
        platform_ids   = form.getlist("platform_id")
        profile_modes  = form.getlist("profile_mode")   # "existing" | "new"
        profile_ids    = form.getlist("profile_id")
        plan_types     = form.getlist("plan_type")
        end_dates      = form.getlist("end_date")
        prices         = form.getlist("price_usd")
        references     = form.getlist("payment_reference")
        # Inline new-profile fields
        new_account_ids    = form.getlist("new_account_id")
        new_profile_names  = form.getlist("new_profile_name")
        new_profile_types  = form.getlist("new_profile_type")
        new_pins           = form.getlist("new_pin")
        new_is_extras      = form.getlist("new_is_extra_member")   # checkbox: "on" or absent
        new_extra_emails   = form.getlist("new_extra_email")
        new_extra_passwords= form.getlist("new_extra_password")

        if not client_name or not platform_ids:
            return RedirectResponse(url="/panel/migrate?error=Faltan+campos+obligatorios", status_code=302)
        if not telegram_id and not username and not phone:
            return RedirectResponse(url="/panel/migrate?error=Debes+proporcionar+ID+username+o+telefono", status_code=302)

        # 1. Upsert user — search by telegram_id or username
        existing_user = None
        if telegram_id:
            res = sb.table("users").select("id, total_purchases").eq("telegram_id", telegram_id).limit(1).execute()
            existing_user = res.data[0] if res.data else None
        if not existing_user and username:
            res = sb.table("users").select("id, total_purchases").eq("username", username).limit(1).execute()
            existing_user = res.data[0] if res.data else None

        if existing_user:
            user_id = existing_user["id"]
            current_purchases = existing_user.get("total_purchases", 0) or 0
            upd: dict = {"name": client_name, "last_seen": venezuela_now().isoformat()}
            if telegram_id: upd["telegram_id"] = telegram_id
            if username: upd["username"] = username
            if phone: upd["phone"] = phone
            if notes: upd["notes"] = notes
            sb.table("users").update(upd).eq("id", user_id).execute()
        else:
            ins: dict = {"name": client_name, "status": "active"}
            if telegram_id: ins["telegram_id"] = telegram_id
            if username: ins["username"] = username
            if phone: ins["phone"] = phone
            if notes: ins["notes"] = notes
            new_user = sb.table("users").insert(ins).execute()
            user_id = new_user.data[0]["id"]
            current_purchases = 0

        # 2. Create one subscription per row
        # Note: all new_* arrays are parallel (same length as platform_ids) because
        # the template uses x-show (not x-if), so fields always exist in DOM.
        created = 0
        for i, pid in enumerate(platform_ids):
            mode     = profile_modes[i] if i < len(profile_modes) else "existing"
            plan     = plan_types[i]  if i < len(plan_types)  else "monthly"
            ed_str   = end_dates[i]   if i < len(end_dates)   else ""
            price    = float(prices[i]) if i < len(prices) and prices[i] else 0.0
            ref      = references[i]  if i < len(references)  else "MIGRADO"

            if not pid or not ed_str:
                continue

            if mode == "new":
                # Create profile inline — fields at same index i
                acc_id   = new_account_ids[i]    if i < len(new_account_ids)    else ""
                pname    = new_profile_names[i]  if i < len(new_profile_names)  else ""
                ptype    = new_profile_types[i]  if i < len(new_profile_types)  else "monthly"
                pin_val  = new_pins[i]            if i < len(new_pins)            else ""
                is_extra = (new_is_extras[i] == "on") if i < len(new_is_extras) else False
                ex_email = new_extra_emails[i]   if i < len(new_extra_emails)   else ""
                ex_pass  = new_extra_passwords[i]if i < len(new_extra_passwords) else ""

                if not acc_id or not pname:
                    continue

                profile_ins: dict = {
                    "platform_id": pid,
                    "account_id": acc_id,
                    "profile_name": pname,
                    "profile_type": ptype,
                    "status": "occupied",
                    "is_extra_member": is_extra,
                }
                if is_extra:
                    if ex_email: profile_ins["extra_email"] = ex_email
                    if ex_pass:  profile_ins["extra_password"] = ex_pass
                else:
                    if pin_val:  profile_ins["pin"] = pin_val

                new_prof = sb.table("profiles").insert(profile_ins).execute()
                prof_id  = new_prof.data[0]["id"]
            else:
                prof_id = profile_ids[i] if i < len(profile_ids) else ""
                if not prof_id:
                    continue

            end_dt = datetime.strptime(ed_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            end_dt = tz_ve.localize(end_dt)

            new_sub = sb.table("subscriptions").insert({
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

            if mode == "existing":
                sb.table("profiles").update({"status": "occupied"}).eq("id", prof_id).execute()
            created += 1

            # Send immediate reminder if expiring within 3 days
            days_left = (end_dt.astimezone(tz_ve) - venezuela_now()).days
            sub_id = new_sub.data[0]["id"] if new_sub.data else None
            if sub_id and days_left <= 3:
                try:
                    from services.notification_service import send_expiry_reminder
                    await send_expiry_reminder(str(sub_id))
                except Exception as notify_err:
                    logger.warning(f"Could not send immediate reminder: {notify_err}")

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
    """Return all profiles for a given platform (used by migrate form JS).
    Includes occupied profiles so admins can link pre-assigned profiles during migration."""
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database import get_supabase
        sb = get_supabase()
        res = sb.table("profiles").select(
            "id, profile_name, profile_type, status, is_extra_member, extra_email"
        ).eq("platform_id", platform_id).order("status").order("profile_type").execute()
        return JSONResponse({"profiles": res.data or []})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@panel_router.get("/api/accounts/by-platform/{platform_id}")
async def api_accounts_by_platform(request: Request, platform_id: str):
    """Return accounts for a given platform (used by migrate inline-profile creation)."""
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database import get_supabase
        sb = get_supabase()
        res = sb.table("accounts").select(
            "id, email"
        ).eq("platform_id", platform_id).eq("status", "active").order("email").execute()
        return JSONResponse({"accounts": res.data or []})
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


@panel_router.get("/api/debug/availability")
async def api_debug_availability(request: Request):
    """Diagnóstico: muestra exactamente lo que el bot ve en disponibilidad de perfiles."""
    if not verify_session(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        from database import get_supabase
        from database.platforms import get_active_platforms
        from database.profiles import get_all_profiles_for_platform
        sb = get_supabase()

        platforms = await get_active_platforms()
        result = []
        for p in platforms:
            pid = p["id"]
            # All profiles for this platform regardless of status/type
            all_profiles = await get_all_profiles_for_platform(pid)
            # What the bot actually queries
            by_type = {}
            for pt in ("monthly", "express", "week"):
                res = sb.table("profiles").select("id, profile_name, status, profile_type") \
                    .eq("platform_id", pid).eq("profile_type", pt).eq("status", "available").execute()
                by_type[pt] = len(res.data or [])

            result.append({
                "platform": p["name"],
                "platform_id": pid,
                "total_profiles_in_db": len(all_profiles),
                "profiles_breakdown": [
                    {"name": pr["profile_name"], "type": pr["profile_type"], "status": pr["status"]}
                    for pr in all_profiles
                ],
                "bot_sees": {
                    "monthly_available": by_type["monthly"],
                    "express_available": by_type["express"],
                    "week_available": by_type["week"],
                }
            })
        return JSONResponse({"platforms": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
