import os
import sqlite3
import secrets
import asyncio
import threading
from datetime import datetime
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

DB = "limon_bd.db"
CONFIG = "config.txt"
STATE = {}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c


def setting(key, default=""):
    c = conn()
    try:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        c.close()


def set_setting(key, value):
    c = conn()
    try:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        c.commit()
    finally:
        c.close()


def init():
    # Never DROP or DELETE tables. Existing SQLite data is preserved.
    c = conn()
    try:
        q = c.execute
        q("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)")
        q("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,tg_id INTEGER UNIQUE,name TEXT,username TEXT,balance REAL DEFAULT 0,ref TEXT UNIQUE,referred_by INTEGER,ref_paid INTEGER DEFAULT 0,created_at TEXT)")
        q("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,reward REAL,link TEXT,active INTEGER DEFAULT 1,created_at TEXT)")
        q("CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,task_id INTEGER,proof TEXT,reward REAL,status TEXT DEFAULT 'pending',created_at TEXT,reviewed_at TEXT)")
        q("CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,method TEXT,amount REAL,trx TEXT,status TEXT DEFAULT 'pending',created_at TEXT,reviewed_at TEXT)")
        q("CREATE TABLE IF NOT EXISTS withdrawals(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,method TEXT,number TEXT,amount REAL,status TEXT DEFAULT 'pending',created_at TEXT,reviewed_at TEXT)")
        q("CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,message TEXT,created_at TEXT,is_read INTEGER DEFAULT 0)")
        defaults = {
            "bkash": "017XXXXXXXX",
            "nagad": "019XXXXXXXX",
            "ref_reward": "2",
            "min_withdraw": "50",
        }
        # Migrate old databases safely: add any columns required by the current bot.
        migrations = {
            "users": {
                "balance": "REAL DEFAULT 0", "ref": "TEXT", "referred_by": "INTEGER", "ref_paid": "INTEGER DEFAULT 0", "created_at": "TEXT", "username": "TEXT", "name": "TEXT"
            },
            "tasks": {
                "title": "TEXT", "description": "TEXT", "reward": "REAL DEFAULT 0", "link": "TEXT", "active": "INTEGER DEFAULT 1", "created_at": "TEXT"
            },
            "submissions": {
                "user_id": "INTEGER", "task_id": "INTEGER", "proof": "TEXT", "reward": "REAL DEFAULT 0", "status": "TEXT DEFAULT 'pending'", "created_at": "TEXT", "reviewed_at": "TEXT"
            },
            "deposits": {
                "user_id": "INTEGER", "method": "TEXT", "amount": "REAL DEFAULT 0", "trx": "TEXT", "status": "TEXT DEFAULT 'pending'", "created_at": "TEXT", "reviewed_at": "TEXT"
            },
            "withdrawals": {
                "user_id": "INTEGER", "method": "TEXT", "number": "TEXT", "amount": "REAL DEFAULT 0", "status": "TEXT DEFAULT 'pending'", "created_at": "TEXT", "reviewed_at": "TEXT"
            },
            "notifications": {
                "user_id": "INTEGER", "message": "TEXT", "created_at": "TEXT", "is_read": "INTEGER DEFAULT 0"
            },
        }
        for table, cols in migrations.items():
            existing = {r[1] for r in q(f"PRAGMA table_info({table})").fetchall()}
            for col, definition in cols.items():
                if col not in existing:
                    q(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

        # Fill safe defaults in migrated rows.
        q("UPDATE users SET balance=0 WHERE balance IS NULL")
        q("UPDATE users SET ref_paid=0 WHERE ref_paid IS NULL")
        q("UPDATE tasks SET active=1 WHERE active IS NULL")
        q("UPDATE submissions SET status='pending' WHERE status IS NULL OR status=''")
        q("UPDATE deposits SET status='pending' WHERE status IS NULL OR status=''")
        q("UPDATE withdrawals SET status='pending' WHERE status IS NULL OR status=''")

        for key, value in defaults.items():
            q("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (key, value))
        c.commit()
    finally:
        c.close()


def load_config():
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("ADMIN_ID", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "12345").strip() or "12345"

    if token and admin_id:
        try:
            return token, int(admin_id), password
        except ValueError:
            raise RuntimeError("ADMIN_ID must be a numeric Telegram user ID.")

    if os.path.exists(CONFIG):
        lines = open(CONFIG, encoding="utf-8").read().splitlines()
        if len(lines) >= 2 and lines[0].strip() and lines[1].strip():
            return lines[0].strip(), int(lines[1].strip()), (lines[2].strip() if len(lines) >= 3 and lines[2].strip() else "12345")

    raise RuntimeError("BOT_TOKEN and ADMIN_ID environment variables are required on Render.")


TOKEN, ADMIN, PASSWORD = load_config()


def clear(uid):
    STATE.pop(uid, None)


def user(tg):
    c = conn()
    try:
        row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg.id,)).fetchone()
        if row is None:
            ref = secrets.token_hex(4).upper()
            c.execute(
                "INSERT INTO users(tg_id,name,username,ref,created_at) VALUES(?,?,?,?,?)",
                (tg.id, tg.full_name, tg.username or "", ref, now()),
            )
            c.commit()
            row = c.execute("SELECT * FROM users WHERE tg_id=?", (tg.id,)).fetchone()
        else:
            c.execute("UPDATE users SET name=?,username=? WHERE tg_id=?", (tg.full_name, tg.username or "", tg.id))
            c.commit()
        return row
    finally:
        c.close()


def menu(uid):
    rows = [
        ["▶️ START"],
        ["💰 Balance", "📋 Tasks"],
        ["💳 Deposit", "💸 Withdraw"],
        ["👥 Referral", "📜 History"],
        ["👤 Profile"],
    ]
    if uid == ADMIN:
        rows.append(["🔐 Admin"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def amenu():
    return ReplyKeyboardMarkup(
        [
            ["➕ Add Task", "📋 Manage Tasks"],
            ["📥 Submissions", "💳 Deposits"],
            ["💸 Withdrawals", "⚙️ Settings"],
            ["📢 Broadcast", "👥 Users"],
            ["🏠 User Menu"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def cancel_menu():
    return ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True, is_persistent=True)


def method_menu():
    return ReplyKeyboardMarkup([["bKash", "Nagad"], ["❌ Cancel"]], resize_keyboard=True, is_persistent=True)


def valid_url(value):
    if not value:
        return False
    try:
        p = urlparse(value.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


async def safe_admin_message(ct, text, reply_markup=None):
    try:
        await asyncio.wait_for(ct.bot.send_message(ADMIN, text, reply_markup=reply_markup), 12)
        return True
    except Exception as e:
        print("ADMIN MESSAGE ERROR:", repr(e))
        return False


async def notify_user(ct, uid, text):
    try:
        await asyncio.wait_for(ct.bot.send_message(uid, text), 12)
        return True
    except Exception as e:
        print("USER NOTIFY ERROR:", uid, repr(e))
        return False


async def start(u, ct):
    tg = u.effective_user
    user(tg)

    if ct.args and ct.args[0].startswith("ref_"):
        code = ct.args[0][4:]
        c = conn()
        try:
            me = c.execute("SELECT * FROM users WHERE tg_id=?", (tg.id,)).fetchone()
            ref_user = c.execute("SELECT * FROM users WHERE ref=?", (code,)).fetchone()
            if ref_user and ref_user["tg_id"] != tg.id and me["referred_by"] is None:
                reward = float(setting("ref_reward", "2"))
                c.execute("UPDATE users SET referred_by=? WHERE tg_id=?", (ref_user["tg_id"], tg.id))
                c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (reward, ref_user["tg_id"]))
                c.execute(
                    "INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)",
                    (ref_user["tg_id"], f"🎉 Referral Bonus +৳{reward:.2f}", now()),
                )
                c.commit()
                await notify_user(ct, ref_user["tg_id"], f"🎉 Referral Bonus +৳{reward:.2f}")
        finally:
            c.close()

    clear(tg.id)
    row = user(tg)
    await u.message.reply_text(
        f"🎉 Limon BD Earning\n\n👋 স্বাগতম {tg.full_name}!\n\n💰 Balance: ৳{row['balance']:.2f}\n📋 Unlimited Tasks\n\nMenu থেকে Option নির্বাচন করুন।",
        reply_markup=menu(tg.id),
    )


async def balance(u, ct):
    uid = u.effective_user.id
    clear(uid)
    row = user(u.effective_user)
    await u.message.reply_text(f"💰 Your Balance\n\n৳{row['balance']:.2f}", reply_markup=menu(uid))


async def profile(u, ct):
    uid = u.effective_user.id
    clear(uid)
    row = user(u.effective_user)
    await u.message.reply_text(
        f"👤 Profile\n\n🆔 {row['tg_id']}\n👤 {row['name']}\n💰 ৳{row['balance']:.2f}\n🎁 {row['ref']}\n📅 {row['created_at']}",
        reply_markup=menu(uid),
    )


async def referral(u, ct):
    uid = u.effective_user.id
    clear(uid)
    row = user(u.effective_user)
    me = await ct.bot.get_me()
    c = conn()
    try:
        count = c.execute("SELECT COUNT(*) AS c FROM users WHERE referred_by=?", (uid,)).fetchone()["c"]
    finally:
        c.close()
    link = f"https://t.me/{me.username}?start=ref_{row['ref']}"
    await u.message.reply_text(
        f"👥 Referral\n\n🎁 প্রতি Referral: ৳{float(setting('ref_reward','2')):.2f}\n👥 Total: {count}\n\n🔗 {link}",
        reply_markup=menu(uid),
    )


async def tasks(u, ct):
    uid = u.effective_user.id
    clear(uid)
    c = conn()
    try:
        rows = c.execute("SELECT * FROM tasks WHERE active=1 ORDER BY id DESC").fetchall()
    finally:
        c.close()

    if not rows:
        await u.message.reply_text("📋 এখন কোনো Task নেই।", reply_markup=menu(uid))
        return

    await u.message.reply_text(f"📋 মোট {len(rows)}টি Task আছে।", reply_markup=menu(uid))

    for t in rows:
        buttons = []
        if valid_url(t["link"]):
            buttons.append(InlineKeyboardButton("🔗 Open Task", url=t["link"].strip()))
        buttons.append(InlineKeyboardButton("📤 Submit Proof", callback_data=f"sub:{t['id']}"))
        await u.message.reply_text(
            f"📌 #{t['id']} {t['title']}\n\n{t['description']}\n\n💰 Reward: ৳{float(t['reward']):.2f}",
            reply_markup=InlineKeyboardMarkup([buttons]),
        )


async def add_task_flow(u, ct, s):
    uid = u.effective_user.id
    text = (u.message.text or "").strip()
    step = s["step"]
    data = s["data"]

    if step == 1:
        if not text:
            await u.message.reply_text("❌ Task Name খালি রাখা যাবে না।", reply_markup=cancel_menu())
            return
        data["title"] = text
        s["step"] = 2
        await u.message.reply_text("Task Description লিখুন:", reply_markup=cancel_menu())
    elif step == 2:
        data["description"] = text or "-"
        s["step"] = 3
        await u.message.reply_text("Reward কত টাকা? শুধু সংখ্যা লিখুন:", reply_markup=cancel_menu())
    elif step == 3:
        try:
            reward = float(text)
            if reward <= 0:
                raise ValueError
        except ValueError:
            await u.message.reply_text("❌ Reward শুধু সংখ্যা হবে। যেমন 3", reply_markup=cancel_menu())
            return
        data["reward"] = reward
        s["step"] = 4
        await u.message.reply_text("Task Link দিন। Link না থাকলে - লিখুন:", reply_markup=cancel_menu())
    else:
        link = text if valid_url(text) else ""
        c = conn()
        try:
            c.execute(
                "INSERT INTO tasks(title,description,reward,link,created_at) VALUES(?,?,?,?,?)",
                (data["title"], data["description"], data["reward"], link, now()),
            )
            c.commit()
        finally:
            c.close()
        clear(uid)
        await u.message.reply_text("✅ Task Added!", reply_markup=amenu())


async def begin_submit(q, uid, tid):
    c = conn()
    try:
        task = c.execute("SELECT id FROM tasks WHERE id=? AND active=1", (tid,)).fetchone()
    finally:
        c.close()
    if not task:
        await q.message.reply_text("❌ Task পাওয়া যায়নি।", reply_markup=menu(uid))
        return

    STATE[uid] = {"flow": "submit", "task": tid}
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done", callback_data=f"done:{tid}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="scancel")],
    ])
    await q.message.reply_text(
        "📋 Task শেষ করে নিচের **Done** বাটনে চাপুন।\n\nআলাদা Proof লিখতে হবে না।",
        reply_markup=kb,
    )


async def submit_done(q, ct, tid):
    uid = q.from_user.id
    # Do not depend on in-memory STATE here. A Render restart/reconnect between
    # Submit Proof and Done must not prevent the submission from being saved.
    # The task id in the button is sufficient to validate the request.

    c = conn()
    try:
        task = c.execute("SELECT * FROM tasks WHERE id=? AND active=1", (tid,)).fetchone()
        if not task:
            clear(uid)
            await q.message.reply_text("❌ Task পাওয়া যায়নি।", reply_markup=menu(uid))
            return

        old = c.execute(
            "SELECT id FROM submissions WHERE user_id=? AND task_id=? AND status='pending'",
            (uid, tid),
        ).fetchone()
        if old:
            clear(uid)
            await q.message.reply_text("⏳ এই Task-এর একটি submission already pending.", reply_markup=menu(uid))
            return

        c.execute(
            "INSERT INTO submissions(user_id,task_id,proof,reward,status,created_at) VALUES(?,?,?,?,?,?)",
            (uid, tid, "Done", float(task["reward"]), "pending", now()),
        )
        sid = c.lastrowid
        c.commit()
    finally:
        c.close()

    clear(uid)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"sa:{sid}"), InlineKeyboardButton("❌ Reject", callback_data=f"sr:{sid}")]])
    admin_text = (
        f"📥 New Task Submission\n\n🆔 Submission: {sid}\n👤 User: {uid}\n"
        f"📌 Task: #{tid} {task['title']}\n💰 Reward: ৳{float(task['reward']):.2f}\n\n📝 Proof: Done"
    )
    sent = await safe_admin_message(ct, admin_text, kb)
    if sent:
        msg = "✅ Submission Admin-এর কাছে পাঠানো হয়েছে। Approval-এর জন্য অপেক্ষা করুন।"
    else:
        msg = "⚠️ Submission Save হয়েছে। Admin Menu → 📥 Submissions থেকে Pending request দেখা যাবে।"
    await q.message.reply_text(msg, reply_markup=menu(uid))


async def deposit_flow(u, ct, s):
    uid = u.effective_user.id
    text = (u.message.text or "").strip()
    step = s["step"]
    data = s["data"]

    if step == 1:
        if text not in ("bKash", "Nagad"):
            await u.message.reply_text("নিচের bKash অথবা Nagad বাটনে চাপুন।", reply_markup=method_menu())
            return
        data["method"] = text
        s["step"] = 2
        await u.message.reply_text(
            f"💳 {text} Number: {setting(text.lower())}\n\nকত টাকা Deposit করবেন?",
            reply_markup=cancel_menu(),
        )
    elif step == 2:
        try:
            amount = float(text)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await u.message.reply_text("❌ সঠিক Amount দিন।", reply_markup=cancel_menu())
            return
        data["amount"] = amount
        s["step"] = 3
        await u.message.reply_text("Transaction ID (TrxID) লিখুন:", reply_markup=cancel_menu())
    else:
        trx = text
        if not trx:
            await u.message.reply_text("❌ TrxID খালি রাখা যাবে না।", reply_markup=cancel_menu())
            return
        c = conn()
        try:
            c.execute(
                "INSERT INTO deposits(user_id,method,amount,trx,status,created_at) VALUES(?,?,?,?,?,?)",
                (uid, data["method"], data["amount"], trx, "pending", now()),
            )
            did = c.lastrowid
            c.commit()
        finally:
            c.close()
        clear(uid)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"da:{did}"), InlineKeyboardButton("❌ Reject", callback_data=f"dr:{did}")]])
        admin_text = (
            f"💳 New Deposit\n\nID: {did}\nUser: {uid}\nMethod: {data['method']}\n"
            f"Amount: ৳{float(data['amount']):.2f}\nTrxID: {trx}"
        )
        sent = await safe_admin_message(ct, admin_text, kb)
        if sent:
            msg = "✅ Deposit request Admin-এর কাছে পাঠানো হয়েছে। Approval-এর জন্য অপেক্ষা করুন।"
        else:
            msg = "⚠️ Deposit Save হয়েছে। Admin Menu → 💳 Deposits থেকে Pending request দেখা যাবে।"
        await u.message.reply_text(msg, reply_markup=menu(uid))


async def withdraw_flow(u, ct, s):
    uid = u.effective_user.id
    text = (u.message.text or "").strip()
    step = s["step"]
    data = s["data"]

    if step == 1:
        if text not in ("bKash", "Nagad"):
            await u.message.reply_text("নিচের bKash অথবা Nagad বাটনে চাপুন।", reply_markup=method_menu())
            return
        data["method"] = text
        s["step"] = 2
        await u.message.reply_text(f"{text} Number লিখুন:", reply_markup=cancel_menu())
    elif step == 2:
        if not text:
            await u.message.reply_text("❌ Number খালি রাখা যাবে না।", reply_markup=cancel_menu())
            return
        data["number"] = text
        s["step"] = 3
        await u.message.reply_text(
            f"Minimum Withdraw: ৳{setting('min_withdraw', '50')}\n\nAmount লিখুন:",
            reply_markup=cancel_menu(),
        )
    else:
        try:
            amount = float(text)
            minimum = float(setting("min_withdraw", "50"))
            if amount < minimum or amount <= 0:
                raise ValueError
        except ValueError:
            await u.message.reply_text(f"❌ Minimum Withdraw ৳{minimum:.2f} এবং সঠিক Amount দিন।", reply_markup=cancel_menu())
            return

        c = conn()
        try:
            row = c.execute("SELECT balance FROM users WHERE tg_id=?", (uid,)).fetchone()
            if not row or float(row["balance"]) < amount:
                await u.message.reply_text("❌ আপনার Balance যথেষ্ট নয়।", reply_markup=cancel_menu())
                return
            c.execute("UPDATE users SET balance=balance-? WHERE tg_id=?", (amount, uid))
            c.execute(
                "INSERT INTO withdrawals(user_id,method,number,amount,status,created_at) VALUES(?,?,?,?,?,?)",
                (uid, data["method"], data["number"], amount, "pending", now()),
            )
            wid = c.lastrowid
            c.commit()
        finally:
            c.close()
        clear(uid)

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"wa:{wid}"), InlineKeyboardButton("❌ Reject", callback_data=f"wr:{wid}")]])
        admin_text = (
            f"💸 New Withdrawal\n\nID: {wid}\nUser: {uid}\nMethod: {data['method']}\n"
            f"Number: {data['number']}\nAmount: ৳{amount:.2f}"
        )
        sent = await safe_admin_message(ct, admin_text, kb)
        if sent:
            msg = "✅ Withdrawal request Admin-এর কাছে পাঠানো হয়েছে। Approval-এর জন্য অপেক্ষা করুন।"
        else:
            msg = "⚠️ Withdrawal Save হয়েছে। Admin Menu → 💸 Withdrawals থেকে Pending request দেখা যাবে।"
        await u.message.reply_text(msg, reply_markup=menu(uid))


async def history(u, ct):
    uid = u.effective_user.id
    clear(uid)
    c = conn()
    try:
        submissions = c.execute("SELECT * FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
        deposits = c.execute("SELECT * FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
        withdrawals = c.execute("SELECT * FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,)).fetchall()
    finally:
        c.close()

    out = "📜 History\n\n📋 Submissions:\n"
    out += "\n".join(f"#{x['id']} Task#{x['task_id']} — {x['status']} — ৳{float(x['reward']):.2f}" for x in submissions) or "None"
    out += "\n\n💳 Deposits:\n"
    out += "\n".join(f"#{x['id']} {x['method']} ৳{float(x['amount']):.2f} — {x['status']}" for x in deposits) or "None"
    out += "\n\n💸 Withdrawals:\n"
    out += "\n".join(f"#{x['id']} {x['method']} ৳{float(x['amount']):.2f} — {x['status']}" for x in withdrawals) or "None"
    await u.message.reply_text(out, reply_markup=menu(uid))


async def admin_menu(u, ct):
    if u.effective_user.id != ADMIN:
        await u.message.reply_text("❌ Access Denied")
        return
    clear(ADMIN)
    STATE[ADMIN] = {"flow": "adminpass"}
    await u.message.reply_text("🔐 Admin Password লিখুন:", reply_markup=cancel_menu())


async def admin_manage(u, ct):
    c = conn()
    try:
        rows = c.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
    finally:
        c.close()
    if not rows:
        await u.message.reply_text("📋 কোনো Task নেই।", reply_markup=amenu())
        return
    for t in rows:
        link_text = t["link"] if valid_url(t["link"]) else "Link নেই"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 Delete", callback_data=f"tdel:{t['id']}")]])
        await u.message.reply_text(f"#{t['id']} {t['title']}\n💰 ৳{float(t['reward']):.2f}\n🔗 {link_text}", reply_markup=kb)


async def admin_list(u, ct, kind):
    table = {"sub": "submissions", "dep": "deposits", "with": "withdrawals"}[kind]
    c = conn()
    try:
        rows = c.execute(f'SELECT * FROM {table} WHERE status="pending" ORDER BY id DESC LIMIT 50').fetchall()
    finally:
        c.close()

    if not rows:
        await u.message.reply_text("✅ কোনো Pending request নেই।", reply_markup=amenu())
        return

    for r in rows:
        if kind == "sub":
            text = f"📥 Submission #{r['id']}\nUser: {r['user_id']}\nTask: #{r['task_id']}\nReward: ৳{float(r['reward']):.2f}\nProof: {r['proof']}"
            approve, reject = f"sa:{r['id']}", f"sr:{r['id']}"
        elif kind == "dep":
            text = f"💳 Deposit #{r['id']}\nUser: {r['user_id']}\n{r['method']}\n৳{float(r['amount']):.2f}\nTrxID: {r['trx']}"
            approve, reject = f"da:{r['id']}", f"dr:{r['id']}"
        else:
            text = f"💸 Withdrawal #{r['id']}\nUser: {r['user_id']}\n{r['method']}\n{r['number']}\n৳{float(r['amount']):.2f}"
            approve, reject = f"wa:{r['id']}", f"wr:{r['id']}"
        await u.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=approve), InlineKeyboardButton("❌ Reject", callback_data=reject)]]),
        )


async def admin_settings(u, ct):
    STATE[ADMIN] = {"flow": "settings", "step": 1}
    kb = ReplyKeyboardMarkup(
        [["💳 bKash", "💳 Nagad"], ["🎁 Referral", "💸 Min Withdraw"], ["❌ Cancel"]],
        resize_keyboard=True,
        is_persistent=True,
    )
    await u.message.reply_text(
        f"⚙️ Settings\n\nbKash: {setting('bkash')}\nNagad: {setting('nagad')}\nReferral: ৳{setting('ref_reward')}\nMinimum Withdraw: ৳{setting('min_withdraw')}\n\nযেটা পরিবর্তন করবেন সেটার বাটনে চাপুন।",
        reply_markup=kb,
    )


async def settings_flow(u, ct, s):
    if s["step"] == 1:
        key = {"💳 bKash": "bkash", "💳 Nagad": "nagad", "🎁 Referral": "ref_reward", "💸 Min Withdraw": "min_withdraw"}.get(u.message.text)
        if not key:
            await u.message.reply_text("নিচের একটি Settings বাটনে চাপুন।")
            return
        s["key"] = key
        s["step"] = 2
        await u.message.reply_text("নতুন Value লিখুন:", reply_markup=cancel_menu())
    else:
        value = (u.message.text or "").strip()
        if not value:
            await u.message.reply_text("❌ Value খালি রাখা যাবে না।", reply_markup=cancel_menu())
            return
        set_setting(s["key"], value)
        clear(ADMIN)
        await u.message.reply_text("✅ Setting Updated.", reply_markup=amenu())


async def admin_users(u, ct):
    c = conn()
    try:
        total = c.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        rows = c.execute("SELECT tg_id,name,balance FROM users ORDER BY id DESC LIMIT 30").fetchall()
    finally:
        c.close()
    text = f"👥 Total Users: {total}\n\n" + ("\n".join(f"{r['tg_id']} | {r['name']} | ৳{float(r['balance']):.2f}" for r in rows) or "None")
    await u.message.reply_text(text, reply_markup=amenu())


async def admin_broadcast(u, ct):
    clear(ADMIN)
    STATE[ADMIN] = {"flow": "broadcast"}
    await u.message.reply_text("📢 যে মেসেজটি সব User-এর কাছে পাঠাতে চান সেটি লিখুন।", reply_markup=cancel_menu())


async def do_broadcast(u, ct):
    msg = (u.message.text or "").strip()
    if not msg:
        await u.message.reply_text("❌ খালি মেসেজ পাঠানো যাবে না।", reply_markup=cancel_menu())
        return

    c = conn()
    try:
        rows = c.execute("SELECT tg_id FROM users").fetchall()
    finally:
        c.close()

    await u.message.reply_text(f"📢 Broadcast শুরু হয়েছে...\n👥 মোট User: {len(rows)}")
    sent = failed = 0
    for row in rows:
        try:
            await asyncio.wait_for(ct.bot.send_message(row["tg_id"], f"📢 Admin Message\n\n{msg}"), 10)
            sent += 1
        except Exception as e:
            failed += 1
            print("BROADCAST ERROR:", row["tg_id"], repr(e))
        await asyncio.sleep(0.05)

    clear(ADMIN)
    await u.message.reply_text(f"✅ Broadcast শেষ।\n\n📤 গেছে: {sent}\n⚠️ যায়নি: {failed}", reply_markup=amenu())


async def process_admin_action(q, ct, action, rid):
    c = conn()
    target = None
    message = None
    try:
        c.execute("BEGIN IMMEDIATE")

        if action in ("sa", "sr"):
            row = c.execute("SELECT * FROM submissions WHERE id=? AND status='pending'", (rid,)).fetchone()
            if not row:
                raise ValueError("already")
            approved = action == "sa"
            c.execute("UPDATE submissions SET status=?,reviewed_at=? WHERE id=?", ("approved" if approved else "rejected", now(), rid))
            if approved:
                c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (float(row["reward"]), row["user_id"]))
            message = f"✅ Task Approved!\n💰 Reward +৳{float(row['reward']):.2f}" if approved else "❌ Task Submission Rejected."
            target = row["user_id"]

        elif action in ("da", "dr"):
            row = c.execute("SELECT * FROM deposits WHERE id=? AND status='pending'", (rid,)).fetchone()
            if not row:
                raise ValueError("already")
            approved = action == "da"
            c.execute("UPDATE deposits SET status=?,reviewed_at=? WHERE id=?", ("approved" if approved else "rejected", now(), rid))
            if approved:
                c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (float(row["amount"]), row["user_id"]))
            message = f"✅ Deposit Approved!\n💰 +৳{float(row['amount']):.2f}" if approved else "❌ Deposit Rejected."
            target = row["user_id"]

        elif action in ("wa", "wr"):
            row = c.execute("SELECT * FROM withdrawals WHERE id=? AND status='pending'", (rid,)).fetchone()
            if not row:
                raise ValueError("already")
            approved = action == "wa"
            c.execute("UPDATE withdrawals SET status=?,reviewed_at=? WHERE id=?", ("approved" if approved else "rejected", now(), rid))
            if not approved:
                c.execute("UPDATE users SET balance=balance+? WHERE tg_id=?", (float(row["amount"]), row["user_id"]))
            message = "✅ Withdrawal Approved." if approved else f"❌ Withdrawal Rejected.\n💰 ৳{float(row['amount']):.2f} Balance-এ ফেরত দেওয়া হয়েছে।"
            target = row["user_id"]

        elif action == "tdel":
            # Soft delete only: old task row remains in database/history.
            c.execute("UPDATE tasks SET active=0 WHERE id=?", (rid,))
            c.commit()
            await q.message.reply_text("🗑 Task deleted.", reply_markup=amenu())
            return
        else:
            raise ValueError("bad action")

        c.execute("INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)", (target, message, now()))
        c.commit()

    except ValueError:
        c.rollback()
        await q.message.reply_text("⚠️ এই Request ইতিমধ্যে processed হয়েছে।", reply_markup=amenu())
        return
    except Exception as e:
        c.rollback()
        print("ADMIN ACTION ERROR:", repr(e))
        await q.message.reply_text("❌ Action সম্পন্ন হয়নি। Admin Menu থেকে Pending request আবার দেখুন।", reply_markup=amenu())
        return
    finally:
        c.close()

    await q.message.reply_text("✅ Done.", reply_markup=amenu())
    await notify_user(ct, target, message)


async def callback(u, ct):
    q = u.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    uid = q.from_user.id
    data = q.data or ""

    try:
        if data.startswith("sub:"):
            await begin_submit(q, uid, int(data.split(":", 1)[1]))
            return
        if data == "scancel":
            clear(uid)
            await q.message.reply_text("❌ Cancel করা হয়েছে।", reply_markup=menu(uid))
            return
        if data.startswith("done:"):
            await submit_done(q, ct, int(data.split(":", 1)[1]))
            return

        if uid != ADMIN:
            return

        if ":" not in data:
            return
        action, rid = data.split(":", 1)
        await process_admin_action(q, ct, action, int(rid))

    except Exception as e:
        print("CALLBACK ERROR:", repr(e))
        try:
            await q.message.reply_text("❌ এই কাজটি সম্পন্ন হয়নি। আবার চেষ্টা করুন।", reply_markup=menu(uid))
        except Exception:
            pass


async def text(u, ct):
    uid = u.effective_user.id
    text_value = (u.message.text or "").strip()

    try:
        if text_value == "❌ Cancel":
            clear(uid)
            await u.message.reply_text("❌ Cancel করা হয়েছে।", reply_markup=amenu() if uid == ADMIN else menu(uid))
            return

        if text_value == "▶️ START":
            await start(u, ct); return
        if text_value == "💰 Balance":
            await balance(u, ct); return
        if text_value == "📋 Tasks":
            await tasks(u, ct); return
        if text_value == "👤 Profile":
            await profile(u, ct); return
        if text_value == "👥 Referral":
            await referral(u, ct); return
        if text_value == "📜 History":
            await history(u, ct); return
        if text_value == "💳 Deposit":
            STATE[uid] = {"flow": "deposit", "step": 1, "data": {}}
            await u.message.reply_text("Deposit Method নির্বাচন করুন:", reply_markup=method_menu()); return
        if text_value == "💸 Withdraw":
            STATE[uid] = {"flow": "withdraw", "step": 1, "data": {}}
            await u.message.reply_text("Withdraw Method নির্বাচন করুন:", reply_markup=method_menu()); return
        if text_value == "🔐 Admin":
            await admin_menu(u, ct); return
        if text_value == "🏠 User Menu":
            clear(uid)
            await u.message.reply_text("👤 User Menu", reply_markup=menu(uid)); return

        if uid == ADMIN:
            if text_value == "➕ Add Task":
                STATE[uid] = {"flow": "add", "step": 1, "data": {}}
                await u.message.reply_text("Task Name লিখুন:", reply_markup=cancel_menu()); return
            if text_value == "📋 Manage Tasks":
                await admin_manage(u, ct); return
            if text_value == "📥 Submissions":
                await admin_list(u, ct, "sub"); return
            if text_value == "💳 Deposits":
                await admin_list(u, ct, "dep"); return
            if text_value == "💸 Withdrawals":
                await admin_list(u, ct, "with"); return
            if text_value == "⚙️ Settings":
                await admin_settings(u, ct); return
            if text_value == "📢 Broadcast":
                await admin_broadcast(u, ct); return
            if text_value == "👥 Users":
                await admin_users(u, ct); return

        state = STATE.get(uid)
        if state:
            flow = state.get("flow")
            if flow == "adminpass":
                if uid == ADMIN and text_value == PASSWORD:
                    clear(uid)
                    STATE[uid] = {"admin_view": True}
                    await u.message.reply_text("✅ Admin Login Successful", reply_markup=amenu())
                else:
                    await u.message.reply_text("❌ Password ভুল।")
            elif flow == "add":
                await add_task_flow(u, ct, state)
            elif flow == "deposit":
                await deposit_flow(u, ct, state)
            elif flow == "withdraw":
                await withdraw_flow(u, ct, state)
            elif flow == "settings":
                await settings_flow(u, ct, state)
            elif flow == "broadcast" and uid == ADMIN:
                await do_broadcast(u, ct)
            return

        await u.message.reply_text("Menu থেকে Option নির্বাচন করুন।", reply_markup=menu(uid))

    except Exception as e:
        print("TEXT ERROR:", repr(e))
        try:
            await u.message.reply_text("❌ একটি সমস্যা হয়েছে। আবার চেষ্টা করুন।", reply_markup=menu(uid))
        except Exception:
            pass


async def err(u, ct):
    print("BOT ERROR:", repr(ct.error))


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"Limon BD Earning Bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    print(f"Health server listening on 0.0.0.0:{port}")
    server.serve_forever()


def main():
    init()
    threading.Thread(target=start_health_server, daemon=True).start()

    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(10)
        .read_timeout(20)
        .write_timeout(20)
        .pool_timeout(10)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.add_error_handler(err)

    print("Limon BD Earning Bot চলছে...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
