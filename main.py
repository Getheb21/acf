"""Cloudflare Auto Signup + Global API Key - Railway Ready."""

import os
import json
import random
import string
import re
import asyncio
import aiohttp
import logging
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = "8222667773:AAHx9yFnP9Ra_fGAxcj0z-u3soFUUiqSbmQ"
GROUP_ID = -1004339334563
MAIL_API = "https://cair.eu.cc"
MAIL_DOMAINS = ["@cair.eu.cc", "@busa.eu.cc", "@besi.eu.cc"]
MAIL_PASSWORD = "Password123!"

app = FastAPI()
telegram_app = None
active_jobs = set()


def generate_password() -> str:
    digits = "".join(random.choices(string.digits, k=3))
    specials = "".join(random.choices("!@#$%^&*", k=2))
    letters = "".join(random.choices(string.ascii_letters, k=7))
    return f"Cf{digits}{letters}{specials}"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class TempMail:
    def __init__(self):
        self.session = None

    async def create(self) -> dict:
        domain = random.choice(MAIL_DOMAINS)
        name = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email = f"{name}{domain}"

        async with aiohttp.ClientSession() as session:
            await session.post(f"{MAIL_API}/accounts", json={"address": email, "password": MAIL_PASSWORD})
            async with session.post(f"{MAIL_API}/token", json={"address": email, "password": MAIL_PASSWORD}) as r:
                data = await r.json()

        return {"email": email, "jwt": data.get("token", "")}

    async def get_messages(self, jwt: str) -> list:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MAIL_API}/messages", headers={"Authorization": f"Bearer {jwt}"}) as r:
                data = await r.json()
        return data.get("hydra:member", [])

    async def get_message(self, jwt: str, msg_id: str) -> dict:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{MAIL_API}/messages/{msg_id}", headers={"Authorization": f"Bearer {jwt}"}) as r:
                data = await r.json()
        return data


async def wait_for_verification_link(jwt: str, timeout: int = 180) -> str:
    """Polling email buat link verifikasi."""
    start = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start) < timeout:
        try:
            messages = await TempMail().get_messages(jwt)
        except Exception:
            await asyncio.sleep(5)
            continue

        for msg in messages:
            msg_id = msg.get("id", "")
            full = await TempMail().get_message(jwt, msg_id)

            text = full.get("text", "") or ""
            html_content = full.get("html", "") or ""
            subject = full.get("subject", "") or ""
            combo = f"{subject}\n{text}\n{html_content}"

            if "cloudflare" not in combo.lower():
                continue

            links = re.findall(r'https?://dash\.cloudflare\.com/email-verification\?token=[^"\s<>]+', combo)
            if links:
                return links[0].rstrip("').,;]\"")

        await asyncio.sleep(5)

    return ""


async def wait_for_otp(jwt: str, timeout: int = 180) -> str:
    """Polling email buat OTP."""
    start = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start) < timeout:
        try:
            messages = await TempMail().get_messages(jwt)
        except Exception:
            await asyncio.sleep(3)
            continue

        for msg in messages:
            msg_id = msg.get("id", "")
            full = await TempMail().get_message(jwt, msg_id)

            text = full.get("text", "") or ""
            html_content = full.get("html", "") or ""
            subject = full.get("subject", "") or ""
            combo = f"{subject}\n{text}\n{html_content}"

            if "verification code" in combo.lower() or "login verification" in combo.lower():
                match = re.search(r'\b(\d{7})\b', combo)
                if match:
                    return match.group(1)

        await asyncio.sleep(3)

    return ""


async def create_cloudflare_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    chat_id = update.effective_chat.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

    password = generate_password()

    # Create email
    mail = TempMail()
    try:
        mail_data = await mail.create()
        email = mail_data["email"]
        jwt = mail_data["jwt"]
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Email gagal: {e}")
        return {"status": "error"}

    await context.bot.send_message(chat_id=chat_id, text=f"📧 `{email}`", parse_mode="Markdown")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
        )
        page = await browser.new_page(viewport={"width": 1280, "height": 720})

        try:
            # Signup
            await page.goto("https://dash.cloudflare.com/sign-up", timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(10)

            # Fill form
            try:
                await page.fill('input[name="email"]', email)
                await asyncio.sleep(1)
                await page.fill('input[name="password"]', password)
                await asyncio.sleep(2)
            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Form error: {e}")
                return {"status": "error"}

            # Tunggu Turnstile (auto-solve kadang bisa)
            await asyncio.sleep(5)

            # Submit
            try:
                await page.click('button[type="submit"]')
                await asyncio.sleep(15)
            except Exception as e:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Submit error: {e}")
                return {"status": "error"}

            # Cek redirect
            url = page.url
            await asyncio.sleep(5)
            url = page.url

            match = re.search(r"/([a-f0-9]{32})", url)
            if not match:
                # Coba screenshot buat debug
                await page.screenshot(path=f"debug_{chat_id}.png")
                await context.bot.send_message(chat_id=chat_id, text="❌ Signup gagal (Turnstile/rate limit)")
                return {"status": "error"}

            account_id = match.group(1)
            await context.bot.send_message(chat_id=chat_id, text=f"🆔 `{account_id}`", parse_mode="Markdown")

            # Verify email
            await context.bot.send_message(chat_id=chat_id, text="📧 Verifikasi email...")
            verify_link = await wait_for_verification_link(jwt)

            if verify_link:
                await page.goto(verify_link, timeout=60000)
                await asyncio.sleep(15)
                await context.bot.send_message(chat_id=chat_id, text="✅ Email verified!")
            else:
                await context.bot.send_message(chat_id=chat_id, text="⚠️ Verifikasi timeout")

            # Request OTP
            await context.bot.send_message(chat_id=chat_id, text="🔐 Request OTP...")
            await page.goto("https://dash.cloudflare.com/profile/api-tokens", timeout=60000)
            await asyncio.sleep(10)

            # Trigger reauthenticate
            await page.evaluate("""
                (async () => {
                    await fetch('/api/v4/user/reauthenticate', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                })()
            """)

            # Polling OTP
            await context.bot.send_message(chat_id=chat_id, text="⏳ Menunggu OTP...")
            otp = await wait_for_otp(jwt)

            if not otp:
                await context.bot.send_message(chat_id=chat_id, text="❌ OTP timeout")
                return {"status": "signup_only", "account_id": account_id, "email": email}

            await context.bot.send_message(chat_id=chat_id, text=f"🔑 OTP: `{otp}`", parse_mode="Markdown")

            # Get API Key
            await context.bot.send_message(chat_id=chat_id, text="🎯 Ambil Global API Key...")
            await asyncio.sleep(5)

            # Coba Turnstile lagi
            await asyncio.sleep(5)

            result = await page.evaluate(f"""
                (async () => {{
                    const cfToken = document.querySelector('input[name="cf-turnstile-response"]')?.value || 
                                   document.querySelector('input[name="cf_challenge_response"]')?.value || '';
                    const body = {{
                        password: "{otp}",
                        cf_challenge_response: cfToken
                    }};
                    const r = await fetch('/api/v4/user/api_key', {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{'Content-Type': 'application/json', 'Accept': 'application/json'}},
                        body: JSON.stringify(body)
                    }});
                    return await r.text();
                }})()
            """)

            api_key = ""
            try:
                resp = json.loads(str(result))
                api_key = resp.get("result", {}).get("api_key", "")
            except Exception:
                api_key = ""

            # Save result
            result_data = {
                "email": email,
                "password": password,
                "account_id": account_id,
                "api_key": api_key,
                "status": "full" if api_key else "signup_only",
                "created_at": timestamp(),
                "created_by": username,
            }

            results_file = "results.json"
            results = []
            if Path(results_file).exists():
                try:
                    with open(results_file) as f:
                        results = json.load(f)
                except Exception:
                    results = []
            results.append(result_data)
            with open(results_file, "w") as f:
                json.dump(results, f, indent=2)

            # Send result
            final_text = (
                f"✅ **AKUN CLOUDFLARE!**\n\n"
                f"📧 `{email}`\n"
                f"🔑 `{password}`\n"
                f"🆔 `{account_id}`\n"
                f"🎯 `{api_key}`\n\n"
                f"BY: {username}"
            )
            await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="Markdown")

            if GROUP_ID:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"Halo {username}\n\n✅ CF Account!\n📧 {email}\n🆔 {account_id}\n🎯 {api_key}"
                )

            await browser.close()
            return result_data

        except Exception as e:
            try:
                await page.screenshot(path=f"debug_{chat_id}.png")
                await browser.close()
            except Exception:
                pass
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
            return {"status": "error"}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id

    if chat_id in active_jobs:
        await update.message.reply_text("⚠️ Job sedang berjalan")
        return

    active_jobs.add(chat_id)
    await update.message.reply_text("🚀 Buat akun Cloudflare...")

    try:
        await create_cloudflare_account(update, context)
    finally:
        active_jobs.discard(chat_id)


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_jobs:
        active_jobs.discard(chat_id)
        await update.message.reply_text("🛑 Dihentikan!")
    else:
        await update.message.reply_text("⚠️ Tidak ada job")


@app.post("/")
async def webhook(request: Request):
    global telegram_app
    try:
        data = await request.json()
        if "message" in data and "text" in data["message"]:
            update = Update.de_json(data, telegram_app.bot)
            if update and update.message:
                await telegram_app.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return {"status": "ok"}


@app.get("/")
async def health_check():
    return Response(content="Bot running!", status_code=200)


@app.on_event("startup")
async def startup_event():
    global telegram_app
    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("stop", stop_command))
    await telegram_app.initialize()
    await telegram_app.start()
    logger.info("Bot ready!")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
