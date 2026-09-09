"""Cloudflare Auto Signup + Global API Key - Bot Telegram."""

import asyncio
import json
import random
import string
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

import httpx
import nodriver as uc
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ============ HARDCODED CONFIG ============
TELEGRAM_TOKEN = "8824915756:AAEYK27n7r3uLATXvJo8yej9A-iuF6ZDHmo"
GROUP_ID = -1004339334563
MAIL_API = "https://cair.eu.cc"
MAIL_DOMAINS = ["@cair.eu.cc", "@busa.eu.cc", "@besi.eu.cc"]
MAIL_PASSWORD = "Password123!"
HEADLESS = True
DELAY_BETWEEN = 30
MAX_LOOP = 5

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
        self.client = httpx.Client(timeout=30)

    def create(self) -> dict:
        domain = random.choice(MAIL_DOMAINS)
        name = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
        email = f"{name}{domain}"

        r = self.client.post(f"{MAIL_API}/accounts", json={"address": email, "password": MAIL_PASSWORD})
        r.raise_for_status()

        r = self.client.post(f"{MAIL_API}/token", json={"address": email, "password": MAIL_PASSWORD})
        data = r.json()

        return {"email": email, "jwt": data.get("token", "")}

    def get_messages(self, jwt: str) -> list:
        r = self.client.get(f"{MAIL_API}/messages", headers={"Authorization": f"Bearer {jwt}"})
        r.raise_for_status()
        return r.json().get("hydra:member", [])

    def get_message(self, jwt: str, msg_id: str) -> dict:
        r = self.client.get(f"{MAIL_API}/messages/{msg_id}", headers={"Authorization": f"Bearer {jwt}"})
        r.raise_for_status()
        return r.json()

    def close(self):
        self.client.close()


async def verify_email(page, jwt: str, timeout: int = 180) -> bool:
    gen = TempMail()
    start = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start) < timeout:
        try:
            messages = gen.get_messages(jwt)
        except Exception:
            await asyncio.sleep(5)
            continue

        for msg in messages:
            msg_id = msg.get("id", "")
            full = gen.get_message(jwt, msg_id) if msg_id else msg

            text = full.get("text", "") or ""
            html_content = full.get("html", "") or ""
            subject = full.get("subject", "") or ""
            combo = f"{subject}\n{text}\n{html_content}"

            if "verify" not in combo.lower() and "confirm" not in combo.lower():
                continue

            links = re.findall(r'https?://dash\.cloudflare\.com/email-verification\?token=[^"\s<>]+', combo)

            for link in links:
                link = link.rstrip("').,;]\"")
                print(f"  [verify] Buka link...")
                await page.get(link)
                await asyncio.sleep(15)

                try:
                    body = str(await page.evaluate("document.body.innerText")).lower()
                except Exception:
                    body = ""

                if any(k in body for k in ("verified", "success", "already verified")):
                    return True

                try:
                    url = str(await page.evaluate("location.href")).lower()
                except Exception:
                    url = ""

                if "dash.cloudflare.com" in url and "login" not in url and "email-verification" not in url:
                    return True

        await asyncio.sleep(5)

    gen.close()
    return False


async def get_otp_from_email(jwt: str, timeout: int = 180) -> str:
    gen = TempMail()
    start = asyncio.get_event_loop().time()

    while (asyncio.get_event_loop().time() - start) < timeout:
        try:
            messages = gen.get_messages(jwt)
        except Exception:
            await asyncio.sleep(3)
            continue

        for msg in messages:
            msg_id = msg.get("id", "")
            full = gen.get_message(jwt, msg_id) if msg_id else msg

            text = full.get("text", "") or ""
            html_content = full.get("html", "") or ""
            subject = full.get("subject", "") or ""
            combo = f"{subject}\n{text}\n{html_content}"

            if "verification code" in combo.lower() or "login verification" in combo.lower():
                match = re.search(r'\b(\d{7})\b', combo)
                if match:
                    return match.group(1)

        await asyncio.sleep(3)

    gen.close()
    return ""


async def create_cloudflare_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    chat_id = update.effective_chat.id
    username = f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name

    password = generate_password()

    mail = TempMail()
    try:
        mail_data = mail.create()
        email = mail_data["email"]
        jwt = mail_data["jwt"]
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Email gagal: {e}")
        mail.close()
        return {"status": "error"}

    await context.bot.send_message(chat_id=chat_id, text=f"📧 `{email}`", parse_mode="Markdown")

    browser = None
    try:
        browser = await uc.start(
            headless=HEADLESS,
            lang="en-US",
            sandbox=False,
            browser_executable_path="/usr/bin/chromium",
        )
        page = await browser.get("https://dash.cloudflare.com/sign-up")
        await asyncio.sleep(10)

        # Fill form
        email_input = None
        for _ in range(6):
            try:
                email_input = await page.select('input[name="email"]', timeout=10)
                if email_input:
                    break
            except Exception:
                pass
            await asyncio.sleep(3)

        if not email_input:
            await context.bot.send_message(chat_id=chat_id, text="❌ Input email tidak ditemukan")
            return {"status": "error"}

        await email_input.click()
        await asyncio.sleep(0.5)
        await email_input.send_keys(email)
        await asyncio.sleep(1)

        pw_input = None
        for _ in range(4):
            try:
                pw_input = await page.select('input[name="password"]', timeout=5)
                if pw_input:
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        if not pw_input:
            await context.bot.send_message(chat_id=chat_id, text="❌ Input password tidak ditemukan")
            return {"status": "error"}

        await pw_input.click()
        await asyncio.sleep(0.5)
        await pw_input.send_keys(password)
        await asyncio.sleep(2)

        # Turnstile
        try:
            await page.verify_cf()
            await asyncio.sleep(3)
        except Exception:
            pass

        # Submit
        try:
            submit_btn = await page.select('button[type="submit"]', timeout=5)
            await submit_btn.scroll_into_view()
            await asyncio.sleep(1)
            await submit_btn.click()
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="❌ Tombol submit tidak ditemukan")
            return {"status": "error"}

        # Wait redirect
        url = ""
        for _ in range(30):
            await asyncio.sleep(1)
            try:
                url = str(await page.evaluate("location.href"))
                if "/sign-up" not in url:
                    break
            except Exception:
                pass

        await asyncio.sleep(5)

        match = re.search(r"/([a-f0-9]{32})", url)
        if not match:
            await context.bot.send_message(chat_id=chat_id, text="❌ Signup gagal")
            return {"status": "error"}

        account_id = match.group(1)
        await context.bot.send_message(chat_id=chat_id, text=f"🆔 Account ID: `{account_id}`", parse_mode="Markdown")

        # Verify email
        await context.bot.send_message(chat_id=chat_id, text="📧 Verifikasi email...")
        verified = await verify_email(page, jwt)

        # Reauthenticate
        await context.bot.send_message(chat_id=chat_id, text="🔐 Request OTP...")
        await page.get("https://dash.cloudflare.com/profile/api-tokens")
        await asyncio.sleep(10)

        try:
            raw = await page.evaluate(
                """
                (async () => {
                    const r = await fetch('/api/v4/user/reauthenticate', {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    return await r.text();
                })()
                """,
                await_promise=True,
                return_by_value=True,
            )
            print(f"Reauth: {raw}")
        except Exception as e:
            print(f"Reauth error: {e}")

        # Polling OTP
        await context.bot.send_message(chat_id=chat_id, text="⏳ Menunggu OTP...")
        otp = await get_otp_from_email(jwt)

        if not otp:
            await context.bot.send_message(chat_id=chat_id, text="❌ OTP timeout")
            return {"status": "signup_only", "account_id": account_id, "email": email}

        await context.bot.send_message(chat_id=chat_id, text=f"🔑 OTP: `{otp}`", parse_mode="Markdown")

        # Get Global API Key
        await context.bot.send_message(chat_id=chat_id, text="🎯 Ambil Global API Key...")

        try:
            await page.verify_cf()
            await asyncio.sleep(3)
        except Exception:
            pass

        try:
            raw = await page.evaluate(
                f"""
                (async () => {{
                    const body = {{
                        password: "{otp}",
                        cf_challenge_response: document.querySelector('input[name="cf-turnstile-response"]')?.value || ''
                    }};
                    const r = await fetch('/api/v4/user/api_key', {{
                        method: 'POST',
                        credentials: 'include',
                        headers: {{'Content-Type': 'application/json', 'Accept': 'application/json'}},
                        body: JSON.stringify(body)
                    }});
                    return await r.text();
                }})()
                """,
                await_promise=True,
                return_by_value=True,
            )

            print(f"API Key: {raw}")
            resp = json.loads(str(raw))
            api_key = resp.get("result", {}).get("api_key", "")
        except Exception as e:
            print(f"API Key error: {e}")
            api_key = ""

        # Save result
        result = {
            "email": email,
            "password": password,
            "account_id": account_id,
            "api_key": api_key,
            "email_verified": verified,
            "status": "full" if api_key else ("signup_only" if account_id else "error"),
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
        results.append(result)
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)

        final_text = (
            f"✅ **AKUN CLOUDFLARE!**\n\n"
            f"📧 Email: `{email}`\n"
            f"🔑 Password: `{password}`\n"
            f"🆔 Account ID: `{account_id}`\n"
            f"🎯 Global API Key: `{api_key}`\n"
            f"✅ Email Verified: {verified}\n\n"
            f"BY: {username}"
        )
        await context.bot.send_message(chat_id=chat_id, text=final_text, parse_mode="Markdown")

        if GROUP_ID:
            group_text = (
                f"Halo {username}\n\n"
                f"✅ Cloudflare account!\n\n"
                f"📧 {email}\n"
                f"🆔 {account_id}\n"
                f"🎯 {api_key}\n\n"
                f"BY: {username}"
            )
            await context.bot.send_message(chat_id=GROUP_ID, text=group_text)

        return result

    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        return {"status": "error"}
    finally:
        mail.close()
        if browser:
            try:
                browser.stop()
            except Exception:
                pass
            await asyncio.sleep(3)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id

    if chat_id in active_jobs:
        await update.message.reply_text("⚠️ Job sedang berjalan")
        return

    active_jobs.add(chat_id)
    await update.message.reply_text("🚀 Buat akun Cloudflare + Global API Key...")

    try:
        await create_cloudflare_account(update, context)
    finally:
        active_jobs.discard(chat_id)


async def loop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    chat_id = update.effective_chat.id

    if chat_id in active_jobs:
        await update.message.reply_text("⚠️ Job sedang berjalan")
        return

    active_jobs.add(chat_id)
    await update.message.reply_text(f"🔄 Loop {MAX_LOOP} akun...")

    success = 0
    for i in range(MAX_LOOP):
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"🚀 [{i+1}/{MAX_LOOP}]")
            result = await create_cloudflare_account(update, context)
            if result.get("status") in ("full", "signup_only"):
                success += 1
        except Exception as e:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ Loop {i+1} error: {e}")
        await asyncio.sleep(DELAY_BETWEEN)

    active_jobs.discard(chat_id)
    await context.bot.send_message(chat_id=chat_id, text=f"🏁 Selesai! {success}/{MAX_LOOP}")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_jobs:
        active_jobs.discard(chat_id)
        await update.message.reply_text("🛑 Dihentikan!")
    else:
        await update.message.reply_text("⚠️ Tidak ada job")


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("loop", loop_command))
    app.add_handler(CommandHandler("stop", stop_command))

    print("🤖 Bot Cloudflare Auto Signup + Global API Key started!")
    app.run_polling()


if __name__ == "__main__":
    main()
