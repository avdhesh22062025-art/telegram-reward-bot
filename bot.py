import os
import sqlite3
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
}
REQUIRED_CHANNELS = [
    x.strip() for x in os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()
]

DB = "bot.db"
TARGET = 100


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = connect()
    con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referrals INTEGER DEFAULT 0,
            eligible INTEGER DEFAULT 0,
            paid INTEGER DEFAULT 0
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            referred_id INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL
        )
    """)
    con.commit()
    con.close()


def add_user(user_id):
    con = connect()
    con.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
    con.commit()
    con.close()


async def joined_all(bot, user_id):
    for channel in REQUIRED_CHANNELS:
        try:
            member = await bot.get_chat_member(channel, user_id)
            if member.status in ("left", "kicked"):
                return False
        except Exception:
            return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)

    if context.args:
        try:
            referrer = int(context.args[0])
        except ValueError:
            referrer = 0

        if referrer and referrer != user_id:
            con = connect()
            exists = con.execute(
                "SELECT 1 FROM referrals WHERE referred_id=?",
                (user_id,)
            ).fetchone()

            if not exists and await joined_all(context.bot, user_id):
                con.execute(
                    "INSERT INTO referrals(referred_id, referrer_id) VALUES(?,?)",
                    (user_id, referrer)
                )
                con.execute(
                    "UPDATE users SET referrals=referrals+1 WHERE user_id=?",
                    (referrer,)
                )
                con.execute(
                    "UPDATE users SET eligible=1 WHERE user_id=? AND referrals>=?",
                    (referrer, TARGET)
                )
                con.commit()

            con.close()

    await update.message.reply_text(
        "Welcome!\n\n"
        "Required channels join karke referral system use karo.\n\n"
        "/ref - referral link\n"
        "/status - referral status"
    )


async def ref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    me = await context.bot.get_me()
    user_id = update.effective_user.id

    await update.message.reply_text(
        f"Your referral link:\n"
        f"https://t.me/{me.username}?start={user_id}"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)

    con = connect()
    row = con.execute(
        "SELECT referrals, eligible, paid FROM users WHERE user_id=?",
        (user_id,)
    ).fetchone()
    con.close()

    await update.message.reply_text(
        f"Valid referrals: {row['referrals']}/{TARGET}\n"
        f"Reward eligible: {'Yes' if row['eligible'] else 'No'}\n"
        f"Reward paid/marked: {'Yes' if row['paid'] else 'No'}\n\n"
        "Eligible hone par reward manually diya jayega."
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    con = connect()
    users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    refs = con.execute("SELECT COUNT(*) c FROM referrals").fetchone()["c"]
    eligible = con.execute(
        "SELECT COUNT(*) c FROM users WHERE eligible=1 AND paid=0"
    ).fetchone()["c"]
    con.close()

    await update.message.reply_text(
        f"Users: {users}\n"
        f"Valid referrals: {refs}\n"
        f"Unpaid eligible: {eligible}"
    )


async def eligible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    con = connect()
    rows = con.execute(
        "SELECT user_id, referrals FROM users WHERE eligible=1 AND paid=0"
    ).fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No eligible users.")
        return

    text = "Eligible users:\n\n"
    for row in rows:
        text += f"{row['user_id']} - {row['referrals']} referrals\n"

    await update.message.reply_text(text)


async def paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args:
        await update.message.reply_text("Use: /paid USER_ID")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid USER_ID")
        return

    con = connect()
    con.execute("UPDATE users SET paid=1 WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

    await update.message.reply_text("User marked as paid.")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, *args):
        pass


def health_server():
    port = int(os.getenv("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")

    init_db()

    Thread(target=health_server, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ref", ref))
    app
