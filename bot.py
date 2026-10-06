import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
}
REQUIRED_CHANNELS = [
    x.strip() for x in os.getenv("REQUIRED_CHANNELS", "").split(",") if x.strip()
]

DB = Path("bot.db")
REFERRAL_TARGET = 100
REWARD_STARS = 15

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Copy .env.example to .env and configure it.")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

def db(sql, params=(), commit=False):
    cur = conn.execute(sql, params)
    if commit:
        conn.commit()
    return cur

def init_db():
    db("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        referrer_id INTEGER,
        referral_count INTEGER NOT NULL DEFAULT 0,
        reward_eligible INTEGER NOT NULL DEFAULT 0,
        reward_paid INTEGER NOT NULL DEFAULT 0,
        joined_required INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""", commit=True)

    db("""CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER NOT NULL,
        referred_id INTEGER NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'valid',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""", commit=True)

def upsert_user(user):
    db("""INSERT INTO users(user_id, username, first_name)
          VALUES (?, ?, ?)
          ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            first_name=excluded.first_name""",
       (user.id, user.username, user.first_name), commit=True)

async def joined_all_required_channels(user_id, context):
    if not REQUIRED_CHANNELS:
        return True
    for channel in REQUIRED_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in {
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            }:
                return False
        except Exception:
            return False
    return True

def join_keyboard():
    rows = []
    for channel in REQUIRED_CHANNELS:
        label = channel if channel.startswith("@") else "Join channel"
        if channel.startswith("@"):
            url = "https://t.me/" + channel[1:]
            rows.append([InlineKeyboardButton(f"Join {label}", url=url)])
    rows.append([InlineKeyboardButton("✅ Verify Membership", callback_data="verify")])
    return InlineKeyboardMarkup(rows)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)

    # /start REFERRER_ID
    referrer_id = None
    if context.args:
        try:
            candidate = int(context.args[0])
            if candidate != user.id:
                referrer_id = candidate
        except ValueError:
            pass

    row = db("SELECT referrer_id FROM users WHERE user_id=?", (user.id,)).fetchone()
    if row and row["referrer_id"] is None and referrer_id:
        # Save only a possible referrer now. Referral becomes valid only after
        # the new user passes required-channel verification.
        db("UPDATE users SET referrer_id=? WHERE user_id=?",
           (referrer_id, user.id), commit=True)

    ok = await joined_all_required_channels(user.id, context)
    if not ok:
        await update.message.reply_text(
            "👋 Welcome!\n\n"
            "Reward earning activate karne ke liye sabhi required channels join karo, "
            "phir Verify Membership dabao.",
            reply_markup=join_keyboard()
        )
        return

    await activate_user(user.id, context, update.message)

async def activate_user(user_id, context, message):
    row = db("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return

    db("UPDATE users SET joined_required=1 WHERE user_id=?", (user_id,), commit=True)

    # Validate referral exactly once.
    if row["referrer_id"]:
        ref = db("SELECT * FROM referrals WHERE referred_id=?", (user_id,)).fetchone()
        if not ref:
            referrer = db("SELECT * FROM users WHERE user_id=?",
                          (row["referrer_id"],)).fetchone()
            if referrer and referrer["user_id"] != user_id:
                db("""INSERT INTO referrals(referrer_id, referred_id, status)
                      VALUES (?, ?, 'valid')""",
                   (row["referrer_id"], user_id), commit=True)
                db("""UPDATE users
                      SET referral_count=referral_count+1,
                          reward_eligible=CASE
                              WHEN referral_count+1 >= ? THEN 1 ELSE reward_eligible END
                      WHERE user_id=?""",
                   (REFERRAL_TARGET, row["referrer_id"]), commit=True)

    updated = db("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    text = (
        "✅ Verification successful!\n\n"
        f"Your valid referrals: {updated['referral_count']}/{REFERRAL_TARGET}\n"
        "1 valid referral = 1 referral point.\n\n"
        "100 valid referrals complete hone par aap reward ke liye eligible honge. "
        "Reward admin manually provide karega."
    )
    await message.reply_text(text)

async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_user(user)

    if not await joined_all_required_channels(user.id, context):
        await query.message.reply_text(
            "❌ Abhi sabhi required channels join nahi hue. Join karke dobara verify karo.",
            reply_markup=join_keyboard()
        )
        return

    await activate_user(user.id, context, query.message)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user)
    row = db("SELECT * FROM users WHERE user_id=?", (user.id,)).fetchone()
    eligible = "YES" if row["reward_eligible"] else "NO"
    paid = "YES" if row["reward_paid"] else "NO"
    await update.message.reply_text(
        f"📊 Your status\n\n"
        f"Valid referrals: {row['referral_count']}/{REFERRAL_TARGET}\n"
        f"Reward eligible: {eligible}\n"
        f"Reward paid: {paid}\n\n"
        "Reward: 15 Telegram Stars, manually provided by the admin."
    )

async def myref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot = await context.bot.get_me()
    link = f"https://t.me/{bot.username}?start={user.id}"
    await update.message.reply_text(
        f"🔗 Your referral link:\n{link}\n\n"
        f"Valid referrals: {db('SELECT referral_count FROM users WHERE user_id=?', (user.id,)).fetchone()['referral_count']}/{REFERRAL_TARGET}"
    )

def is_admin(user_id):
    return user_id in ADMIN_IDS

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    users = db("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    referrals = db("SELECT COUNT(*) AS n FROM referrals WHERE status='valid'").fetchone()["n"]
    eligible = db("SELECT COUNT(*) AS n FROM users WHERE reward_eligible=1 AND reward_paid=0").fetchone()["n"]
    await update.message.reply_text(
        f"👑 Admin stats\nUsers: {users}\nValid referrals: {referrals}\nUnpaid eligible rewards: {eligible}"
    )

async def admin_eligible(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = db("""SELECT user_id, username, referral_count
                 FROM users WHERE reward_eligible=1 AND reward_paid=0
                 ORDER BY referral_count DESC""").fetchall()
    if not rows:
        await update.message.reply_text("No unpaid eligible users.")
        return
    lines = ["🎁 Eligible rewards:"]
    for r in rows[:50]:
        lines.append(f"{r['user_id']} | @{r['username'] or '-'} | {r['referral_count']} referrals")
    await update.message.reply_text("\n".join(lines))

async def admin_paid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Usage: /paid USER_ID")
        return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid USER_ID.")
        return
    row = db("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        await update.message.reply_text("User not found.")
        return
    if not row["reward_eligible"]:
        await update.message.reply_text("User has not reached 100 valid referrals.")
        return
    db("UPDATE users SET reward_paid=1 WHERE user_id=?", (uid,), commit=True)
    await update.message.reply_text(
        f"Marked reward as paid for {uid}. Send/confirm the 15 Telegram Stars separately."
    )

async def error_handler(update, context):
    print("ERROR:", context.error)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("ref", myref))
    app.add_handler(CommandHandler("admin_stats", admin_stats))
    app.add_handler(CommandHandler("eligible", admin_eligible))
    app.add_handler(CommandHandler("paid", admin_paid))
    app.add_handler(CallbackQueryHandler(verify, pattern="^verify$"))
    app.add_error_handler(error_handler)

    print("Bot running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
