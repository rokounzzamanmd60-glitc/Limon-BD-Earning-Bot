import os
import sqlite3
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import (
    Update, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

DB = "limon_bd.db"
STATE = {}

TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN = os.getenv("ADMIN_ID", "").strip()
PASSWORD = os.getenv("ADMIN_PASSWORD", "12345").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not ADMIN.isdigit():
    raise RuntimeError("ADMIN_ID must be a number")

ADMIN = int(ADMIN)


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def setting(key, default=""):
    c = conn()
    r = c.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()
    c.close()
    return r["value"] if r else default


def set_setting(key, value):
    c = conn()
    c.execute(
        """INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, str(value))
    )
    c.commit()
    c.close()


def init():
    c = conn()
    q = c.execute

    q("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY,
        value TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE,
        name TEXT,
        username TEXT,
        balance REAL DEFAULT 0,
        ref TEXT UNIQUE,
        referred_by INTEGER,
        created_at TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS tasks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        description TEXT,
        reward REAL,
        link TEXT,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS submissions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id INTEGER,
        proof TEXT,
        reward REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        reviewed_at TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS deposits(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        method TEXT,
        amount REAL,
        trx TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        reviewed_at TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS withdrawals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        method TEXT,
        number TEXT,
        amount REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        reviewed_at TEXT
    )""")

    q("""CREATE TABLE IF NOT EXISTS notifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        created_at TEXT,
        is_read INTEGER DEFAULT 0
    )""")

    defaults = {
        "bkash": "017XXXXXXXX",
        "nagad": "019XXXXXXXX",
        "ref_reward": "2",
        "min_withdraw": "50"
    }

    for k, v in defaults.items():
        q(
            "INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",
            (k, v)
        )

    c.commit()
    c.close()


def get_user(tg):
    c = conn()

    r = c.execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg.id,)
    ).fetchone()

    if not r:
        ref = secrets.token_hex(4).upper()

        c.execute(
            """INSERT INTO users
            (tg_id,name,username,ref,created_at)
            VALUES(?,?,?,?,?)""",
            (
                tg.id,
                tg.full_name,
                tg.username or "",
                ref,
                now()
            )
        )

        c.commit()

        r = c.execute(
            "SELECT * FROM users WHERE tg_id=?",
            (tg.id,)
        ).fetchone()

    else:
        c.execute(
            """UPDATE users
            SET name=?, username=?
            WHERE tg_id=?""",
            (
                tg.full_name,
                tg.username or "",
                tg.id
            )
        )
        c.commit()

    c.close()
    return r


def user_menu(uid):
    rows = [
        ["🏠 Home", "📋 Tasks"],
        ["📤 Submit", "💳 Deposit"],
        ["💸 Withdraw", "👥 Referral"],
        ["📜 History", "👤 Profile"]
    ]

    if uid == ADMIN:
        rows.append(["🔐 Admin"])

    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["➕ Add Task", "📋 Manage Tasks"],
            ["📥 Submissions", "💳 Deposits"],
            ["💸 Withdrawals", "⚙️ Settings"],
            ["👥 Users", "🏠 Home"]
        ],
        resize_keyboard=True,
        is_persistent=True
    )


def clear(uid):
    STATE.pop(uid, None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    get_user(tg)

    if context.args:
        arg = context.args[0]

        if arg.startswith("ref_"):
            code = arg[4:]
            c = conn()

            me = c.execute(
                "SELECT * FROM users WHERE tg_id=?",
                (tg.id,)
            ).fetchone()

            ref_user = c.execute(
                "SELECT * FROM users WHERE ref=?",
                (code,)
            ).fetchone()

            if (
                ref_user
                and ref_user["tg_id"] != tg.id
                and me["referred_by"] is None
            ):
                reward = float(setting("ref_reward", "2"))

                c.execute(
                    "UPDATE users SET referred_by=? WHERE tg_id=?",
                    (ref_user["tg_id"], tg.id)
                )

                c.execute(
                    "UPDATE users SET balance=balance+? WHERE tg_id=?",
                    (reward, ref_user["tg_id"])
                )

                c.execute(
                    """INSERT INTO notifications
                    (user_id,message,created_at)
                    VALUES(?,?,?)""",
                    (
                        ref_user["tg_id"],
                        f"🎉 Referral Bonus +৳{reward:.2f}",
                        now()
                    )
                )

                c.commit()

            c.close()

    clear(tg.id)
    r = get_user(tg)

    await update.message.reply_text(
        f"""🎉 *Limon BD Earning*

👋 স্বাগতম {tg.full_name}!

💰 Balance: ৳{r["balance"]:.2f}
📋 Unlimited Tasks

Menu থেকে Option নির্বাচন করুন।""",
        parse_mode="Markdown",
        reply_markup=user_menu(tg.id)
    )


async def home(update, context):
    uid = update.effective_user.id
    clear(uid)
    r = get_user(update.effective_user)

    await update.message.reply_text(
        f"🏠 *Home*\n\n💰 Balance: ৳{r['balance']:.2f}",
        parse_mode="Markdown",
        reply_markup=user_menu(uid)
    )


async def profile(update, context):
    uid = update.effective_user.id
    clear(uid)
    r = get_user(update.effective_user)

    await update.message.reply_text(
        f"""👤 *Profile*

🆔 `{r["tg_id"]}`
👤 {r["name"]}
💰 ৳{r["balance"]:.2f}
🎁 `{r["ref"]}`
📅 {r["created_at"]}""",
        parse_mode="Markdown",
        reply_markup=user_menu(uid)
    )


async def referral(update, context):
    uid = update.effective_user.id
    clear(uid)

    r = get_user(update.effective_user)

    bot = await context.bot.get_me()

    c = conn()
    count = c.execute(
        "SELECT COUNT(*) AS c FROM users WHERE referred_by=?",
        (uid,)
    ).fetchone()["c"]
    c.close()

    link = f"https://t.me/{bot.username}?start=ref_{r['ref']}"

    await update.message.reply_text(
        f"""👥 *Referral*

🎁 প্রতি Referral: ৳{float(setting("ref_reward","2")):.2f}
👥 Total: {count}

🔗 `{link}`""",
        parse_mode="Markdown",
        reply_markup=user_menu(uid)
    )


async def tasks(update, context):
    uid = update.effective_user.id
    clear(uid)

    c = conn()

    rows = c.execute(
        "SELECT * FROM tasks WHERE active=1 ORDER BY id DESC"
    ).fetchall()

    c.close()

    if not rows:
        await update.message.reply_text(
            "📋 এখন কোনো Task নেই।",
            reply_markup=user_menu(uid)
        )
        return

    await update.message.reply_text(
        f"📋 মোট {len(rows)}টি Task আছে।"
    )

    for t in rows:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📤 Submit Proof",
                    callback_data=f"sub:{t['id']}"
                )
            ]
        ])

        await update.message.reply_text(
            f"""📌 *#{t["id"]} {t["title"]}*

{t["description"]}

💰 Reward: ৳{t["reward"]:.2f}
🔗 {t["link"] or "নেই"}""",
            parse_mode="Markdown",
            reply_markup=keyboard
        )


async def add_task(update, context, state):
    uid = update.effective_user.id
    text = update.message.text
    step = state["step"]
    data = state["data"]

    if step == 1:
        data["title"] = text
        state["step"] = 2
        await update.message.reply_text(
            "Task Description লিখুন:"
        )

    elif step == 2:
        data["description"] = text
        state["step"] = 3
        await update.message.reply_text(
            "Reward কত টাকা? শুধু সংখ্যা লিখুন:"
        )

    elif step == 3:
        try:
            data["reward"] = float(text)
            state["step"] = 4

            await update.message.reply_text(
                "Task Link দিন। না থাকলে - লিখুন:"
            )

        except ValueError:
            await update.message.reply_text(
                "❌ Reward শুধু সংখ্যা হবে। যেমন: 10"
            )

    else:
        data["link"] = "" if text == "-" else text

        c = conn()

        c.execute(
            """INSERT INTO tasks
            (title,description,reward,link,created_at)
            VALUES(?,?,?,?,?)""",
            (
                data["title"],
                data["description"],
                data["reward"],
                data["link"],
                now()
            )
        )

        c.commit()
        c.close()

        clear(uid)

        await update.message.reply_text(
            "✅ Task Added!",
            reply_markup=admin_menu()
        )


async def submit(update, context, state):
    uid = update.effective_user.id
    proof = update.message.text
    task_id = state["task"]

    c = conn()

    task = c.execute(
        "SELECT * FROM tasks WHERE id=? AND active=1",
        (task_id,)
    ).fetchone()

    if not task:
        c.close()
        clear(uid)
        await update.message.reply_text("❌ Task পাওয়া যায়নি।")
        return

    pending = c.execute(
        """SELECT id FROM submissions
        WHERE user_id=? AND task_id=? AND status='pending'""",
        (uid, task_id)
    ).fetchone()

    if pending:
        c.close()
        clear(uid)
        await update.message.reply_text(
            "⏳ এই Task-এর submission already pending।"
        )
        return

    c.execute(
        """INSERT INTO submissions
        (user_id,task_id,proof,reward,created_at)
        VALUES(?,?,?,?,?)""",
        (
            uid,
            task_id,
            proof,
            task["reward"],
            now()
        )
    )

    sid = c.lastrowid
    c.commit()
    c.close()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"sa:{sid}"
            ),
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"sr:{sid}"
            )
        ]
    ])

    await context.bot.send_message(
        ADMIN,
        f"""📥 *New Task Submission*

🆔 Submission: {sid}
👤 User: {uid}
📌 Task: #{task_id} {task["title"]}
💰 Reward: ৳{task["reward"]:.2f}

📝 Proof:
{proof}""",
        parse_mode="Markdown",
        reply_markup=keyboard
    )

    clear(uid)

    await update.message.reply_text(
        "✅ Proof Admin-এর কাছে পাঠানো হয়েছে। Approval-এর জন্য অপেক্ষা করুন।",
        reply_markup=user_menu(uid)
    )


async def deposit(update, context, state):
    uid = update.effective_user.id
    text = update.message.text
    step = state["step"]
    data = state["data"]

    if step == 1:
        if text not in ("bKash", "Nagad"):
            await update.message.reply_text(
                "bKash অথবা Nagad লিখুন।"
            )
            return

        data["method"] = text
        state["step"] = 2

        number = setting(text.lower(), "")

        await update.message.reply_text(
            f"💳 {text} Number: {number}\n\nকত টাকা Deposit করবেন?"
        )

    elif step == 2:
        try:
            amount = float(text)

            if amount <= 0:
                raise ValueError

            data["amount"] = amount
            state["step"] = 3

            await update.message.reply_text(
                "Transaction ID (TrxID) লিখুন:"
            )

        except ValueError:
            await update.message.reply_text(
                "❌ সঠিক Amount দিন।"
            )

    else:
        data["trx"] = text

        c = conn()

        c.execute(
            """INSERT INTO deposits
            (user_id,method,amount,trx,created_at)
            VALUES(?,?,?,?,?)""",
            (
                uid,
                data["method"],
                data["amount"],
                data["trx"],
                now()
            )
        )

        did = c.lastrowid
        c.commit()
        c.close()

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"da:{did}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"dr:{did}"
                )
            ]
        ])

        await context.bot.send_message(
            ADMIN,
            f"""💳 *New Deposit*

ID: {did}
User: {uid}
Method: {data["method"]}
Amount: ৳{data["amount"]:.2f}
TrxID: `{data["trx"]}`""",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

        clear(uid)

        await update.message.reply_text(
            "✅ Deposit request পাঠানো হয়েছে।",
            reply_markup=user_menu(uid)
        )


async def withdraw(update, context, state):
    uid = update.effective_user.id
    text = update.message.text
    step = state["step"]
    data = state["data"]

    if step == 1:
        if text not in ("bKash", "Nagad"):
            await update.message.reply_text(
                "bKash অথবা Nagad লিখুন।"
            )
            return

        data["method"] = text
        state["step"] = 2

        await update.message.reply_text(
            f"{text} Number লিখুন:"
        )

    elif step == 2:
        data["number"] = text
        state["step"] = 3

        await update.message.reply_text(
            f"Minimum Withdraw: ৳{setting('min_withdraw','50')}\n\nAmount লিখুন:"
        )

    else:
        try:
            amount = float(text)
            minimum = float(setting("min_withdraw", "50"))

            if amount < minimum:
                await update.message.reply_text(
                    f"❌ Minimum Withdraw ৳{minimum:.2f}"
                )
                return

        except ValueError:
            await update.message.reply_text(
                "❌ সঠিক Amount দিন।"
            )
            return

        c = conn()

        r = c.execute(
            "SELECT balance FROM users WHERE tg_id=?",
            (uid,)
        ).fetchone()

        if not r or r["balance"] < amount:
            c.close()

            await update.message.reply_text(
                "❌ আপনার Balance যথেষ্ট নয়।"
            )
            return

        c.execute(
            "UPDATE users SET balance=balance-? WHERE tg_id=?",
            (amount, uid)
        )

        c.execute(
            """INSERT INTO withdrawals
            (user_id,method,number,amount,created_at)
            VALUES(?,?,?,?,?)""",
            (
                uid,
                data["method"],
                data["number"],
                amount,
                now()
            )
        )

        wid = c.lastrowid
        c.commit()
        c.close()

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=f"wa:{wid}"
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=f"wr:{wid}"
                )
            ]
        ])

        await context.bot.send_message(
            ADMIN,
            f"""💸 *New Withdrawal*

ID: {wid}
User: {uid}
Method: {data["method"]}
Number: {data["number"]}
Amount: ৳{amount:.2f}""",
            parse_mode="Markdown",
            reply_markup=keyboard
        )

        clear(uid)

        await update.message.reply_text(
            "✅ Withdrawal request পাঠানো হয়েছে।",
            reply_markup=user_menu(uid)
        )


async def history(update, context):
    uid = update.effective_user.id
    clear(uid)

    c = conn()

    submissions = c.execute(
        """SELECT * FROM submissions
        WHERE user_id=? ORDER BY id DESC LIMIT 10""",
        (uid,)
    ).fetchall()

    deposits = c.execute(
        """SELECT * FROM deposits
        WHERE user_id=? ORDER BY id DESC LIMIT 10""",
        (uid,)
    ).fetchall()

    withdrawals = c.execute(
        """SELECT * FROM withdrawals
        WHERE user_id=? ORDER BY id DESC LIMIT 10""",
        (uid,)
    ).fetchall()

    c.close()

    out = "📜 *History*\n\n"

    out += "📋 Submissions:\n"

    if submissions:
        for x in submissions:
            out += (
                f"#{x['id']} Task#{x['task_id']} — "
                f"{x['status']} — ৳{x['reward']:.2f}\n"
            )
    else:
        out += "None\n"

    out += "\n💳 Deposits:\n"

    if deposits:
        for x in deposits:
            out += (
                f"#{x['id']} {x['method']} "
                f"৳{x['amount']:.2f} — {x['status']}\n"
            )
    else:
        out += "None\n"

    out += "\n💸 Withdrawals:\n"

    if withdrawals:
        for x in withdrawals:
            out += (
                f"#{x['id']} {x['method']} "
                f"৳{x['amount']:.2f} — {x['status']}\n"
            )
    else:
        out += "None"

    await update.message.reply_text(
        out,
        parse_mode="Markdown",
        reply_markup=user_menu(uid)
    )


async def admin_login(update, context):
    uid = update.effective_user.id

    if uid != ADMIN:
        await update.message.reply_text(
            "❌ Access Denied"
        )
        return

    STATE[uid] = {
        "flow": "adminpass"
    }

    await update.message.reply_text(
        "🔐 Admin Password লিখুন:"
    )


async def manage_tasks(update, context):
    c = conn()

    rows = c.execute(
        "SELECT * FROM tasks ORDER BY id DESC"
    ).fetchall()

    c.close()

    if not rows:
        await update.message.reply_text(
            "📋 কোনো Task নেই।",
            reply_markup=admin_menu()
        )
        return

    for t in rows:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=f"tdel:{t['id']}"
                )
            ]
        ])

        await update.message.reply_text(
            f"""#{t["id"]} {t["title"]}
💰 ৳{t["reward"]:.2f}
🔗 {t["link"] or "-"}""",
            reply_markup=keyboard
        )


async def admin_list(update, context, kind):
    tables = {
        "sub": "submissions",
        "dep": "deposits",
        "with": "withdrawals"
    }

    table = tables[kind]

    c = conn()

    rows = c.execute(
        f"""SELECT * FROM {table}
        WHERE status='pending'
        ORDER BY id DESC LIMIT 30"""
    ).fetchall()

    c.close()

    if not rows:
        await update.message.reply_text(
            "✅ কোনো Pending request নেই।",
            reply_markup=admin_menu()
        )
        return

    for r in rows:

        if kind == "sub":
            text = (
                f"📥 Submission #{r['id']}\n"
                f"User: {r['user_id']}\n"
                f"Task: #{r['task_id']}\n"
                f"Reward: ৳{r['reward']}\n"
                f"Proof: {r['proof']}"
            )

            approve = f"sa:{r['id']}"
            reject = f"sr:{r['id']}"

        elif kind == "dep":
            text = (
                f"💳 Deposit #{r['id']}\n"
                f"User: {r['user_id']}\n"
                f"{r['method']}\n"
                f"৳{r['amount']}\n"
                f"Trx: {r['trx']}"
            )

            approve = f"da:{r['id']}"
            reject = f"dr:{r['id']}"

        else:
            text = (
                f"💸 Withdrawal #{r['id']}\n"
                f"User: {r['user_id']}\n"
                f"{r['method']}\n"
                f"{r['number']}\n"
                f"৳{r['amount']}"
            )

            approve = f"wa:{r['id']}"
            reject = f"wr:{r['id']}"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Approve",
                    callback_data=approve
                ),
                InlineKeyboardButton(
                    "❌ Reject",
                    callback_data=reject
                )
            ]
        ])

        await update.message.reply_text(
            text,
            reply_markup=keyboard
        )


async def settings(update, context):
    STATE[ADMIN] = {
        "flow": "settings",
        "step": 1
    }

    await update.message.reply_text(
        f"""⚙️ Settings

1. bKash: {setting("bkash")}
2. Nagad: {setting("nagad")}
3. Referral: ৳{setting("ref_reward")}
4. Minimum Withdraw: ৳{setting("min_withdraw")}

কোনটি বদলাবেন? 1/2/3/4"""
    )


async def settings_flow(update, context, state):
    text = update.message.text

    if state["step"] == 1:

        if text not in ("1", "2", "3", "4"):
            await update.message.reply_text(
                "1/2/3/4 লিখুন।"
            )
            return

        state["key"] = {
            "1": "bkash",
            "2": "nagad",
            "3": "ref_reward",
            "4": "min_withdraw"
        }[text]

        state["step"] = 2

        await update.message.reply_text(
            "নতুন Value লিখুন:"
        )

    else:
        set_setting(
            state["key"],
            text
        )

        clear(ADMIN)

        await update.message.reply_text(
            "✅ Setting Updated.",
            reply_markup=admin_menu()
        )


async def users(update, context):
    c = conn()

    total = c.execute(
        "SELECT COUNT(*) AS c FROM users"
    ).fetchone()["c"]

    rows = c.execute(
        """SELECT tg_id,name,balance
        FROM users ORDER BY id DESC LIMIT 20"""
    ).fetchall()

    c.close()

    text = f"👥 Total Users: {total}\n\n"

    for r in rows:
        text += (
            f"{r['tg_id']} | "
            f"{r['name']} | "
            f"৳{r['balance']:.2f}\n"
        )

    await update.message.reply_text(
        text,
        reply_markup=admin_menu()
    )


async def callback(update, context):
    query = update.callback_query

    await query.answer()

    uid = query.from_user.id
    data = query.data

    if data.startswith("sub:"):
        task_id = int(data.split(":")[1])

        STATE[uid] = {
            "flow": "submit",
            "task": task_id
        }

        await query.message.reply_text(
            "📤 Proof পাঠান।"
        )
        return

    if uid != ADMIN:
        return

    action, value = data.split(":")
    rid = int(value)

    c = conn()

    try:
        c.execute("BEGIN IMMEDIATE")

        if action in ("sa", "sr"):

            r = c.execute(
                """SELECT * FROM submissions
                WHERE id=? AND status='pending'""",
                (rid,)
            ).fetchone()

            if not r:
                raise ValueError

            if action == "sa":
                status = "approved"

                c.execute(
                    """UPDATE users
                    SET balance=balance+?
                    WHERE tg_id=?""",
                    (r["reward"], r["user_id"])
                )

                message = (
                    f"✅ Task Approved!\n"
                    f"💰 Reward +৳{r['reward']:.2f}"
                )

            else:
                status = "rejected"
                message = "❌ Task Submission Rejected."

            c.execute(
                """UPDATE submissions
                SET status=?,reviewed_at=?
                WHERE id=?""",
                (status, now(), rid)
            )

        elif action in ("da", "dr"):

            r = c.execute(
                """SELECT * FROM deposits
                WHERE id=? AND status='pending'""",
                (rid,)
            ).fetchone()

            if not r:
                raise ValueError

            if action == "da":
                status = "approved"

                c.execute(
                    """UPDATE users
                    SET balance=balance+?
                    WHERE tg_id=?""",
                    (r["amount"], r["user_id"])
                )

                message = (
                    f"✅ Deposit Approved!\n"
                    f"💰 +৳{r['amount']:.2f}"
                )

            else:
                status = "rejected"
                message = "❌ Deposit Rejected."

            c.execute(
                """UPDATE deposits
                SET status=?,reviewed_at=?
                WHERE id=?""",
                (status, now(), rid)
            )

        elif action in ("wa", "wr"):

            r = c.execute(
                """SELECT * FROM withdrawals
                WHERE id=? AND status='pending'""",
                (rid,)
            ).fetchone()

            if not r:
                raise ValueError

            if action == "wa":
                status = "approved"
                message = "✅ Withdrawal Approved."

            else:
                status = "rejected"

                c.execute(
                    """UPDATE users
                    SET balance=balance+?
                    WHERE tg_id=?""",
                    (r["amount"], r["user_id"])
                )

                message = (
                    "❌ Withdrawal Rejected.\n"
                    "💰 Amount refunded to your Balance."
                )

            c.execute(
                """UPDATE withdrawals
                SET status=?,reviewed_at=?
                WHERE id=?""",
                (status, now(), rid)
            )

        elif action == "tdel":

            c.execute(
                "UPDATE tasks SET active=0 WHERE id=?",
                (rid,)
            )

            c.commit()
            c.close()

            await query.message.reply_text(
                "🗑 Task deleted."
            )
            return

        else:
            raise ValueError

        c.execute(
            """INSERT INTO notifications
            (user_id,message,created_at)
            VALUES(?,?,?)""",
            (
                r["user_id"],
                message,
                now()
            )
        )

        c.commit()

        target = r["user_id"]

    except ValueError:

        c.rollback()
        c.close()

        await query.message.reply_text(
            "⚠️ এই Request ইতিমধ্যে processed হয়েছে।"
        )
        return

    c.close()

    await query.message.reply_text(
        "✅ Done."
    )

    try:
        await context.bot.send_message(
            target,
            message
        )
    except Exception:
        pass


async def text_handler(update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()

    if text == "🏠 Home":
        return await home(update, context)

    if text == "📋 Tasks":
        return await tasks(update, context)

    if text == "👤 Profile":
        return await profile(update, context)

    if text == "👥 Referral":
        return await referral(update, context)

    if text == "📜 History":
        return await history(update, context)

    if text == "📤 Submit":
        await update.message.reply_text(
            "📋 Tasks থেকে একটি Task-এর Submit Proof চাপুন।"
        )
        return

    if text == "💳 Deposit":
        STATE[uid] = {
            "flow": "deposit",
            "step": 1,
            "data": {}
        }

        await update.message.reply_text(
            "Deposit Method লিখুন: bKash অথবা Nagad"
        )
        return

    if text == "💸 Withdraw":
        STATE[uid] = {
            "flow": "withdraw",
            "step": 1,
            "data": {}
        }

        await update.message.reply_text(
            "Withdraw Method লিখুন: bKash অথবা Nagad"
        )
        return

    if text == "🔐 Admin":
        return await admin_login(update, context)

    if uid == ADMIN:

        if text == "➕ Add Task":
            STATE[uid] = {
                "flow": "add",
                "step": 1,
                "data": {}
            }

            await update.message.reply_text(
                "Task Name লিখুন:"
            )
            return

        if text == "📋 Manage Tasks":
            return await manage_tasks(update, context)

        if text == "📥 Submissions":
            return await admin_list(update, context, "sub")

        if text == "💳 Deposits":
            return await admin_list(update, context, "dep")

        if text == "💸 Withdrawals":
            return await admin_list(update, context, "with")

        if text == "⚙️ Settings":
            return await settings(update, context)

        if text == "👥 Users":
            return await users(update, context)

    state = STATE.get(uid)

    if state:

        if state["flow"] == "adminpass":

            if uid == ADMIN and text == PASSWORD:
                clear(uid)

                await update.message.reply_text(
                    "✅ Admin Login Successful",
                    reply_markup=admin_menu()
                )

            else:
                await update.message.reply_text(
                    "❌ Password ভুল।"
                )

        elif state["flow"] == "add":
            await add_task(update, context, state)

        elif state["flow"] == "submit":
            await submit(update, context, state)

        elif state["flow"] == "deposit":
            await deposit(update, context, state)

        elif state["flow"] == "withdraw":
            await withdraw(update, context, state)

        elif state["flow"] == "settings":
            await settings_flow(update, context, state)

        return

    await update.message.reply_text(
        "Menu থেকে Option নির্বাচন করুন।",
        reply_markup=user_menu(uid)
    )


async def error_handler(update, context):
    print("ERROR:", context.error)


def health_server():
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):

        def do_GET(self):
            body = b"Limon BD Earning Bot is running"

            self.send_response(200)
            self.send_header(
                "Content-Type",
                "text/plain"
            )
            self.send_header(
                "Content-Length",
                str(len(body))
            )
            self.end_headers()

            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(
        ("0.0.0.0", port),
        Handler
    )

    print("Health server running on port", port)

    server.serve_forever()


def main():

    init()

    threading.Thread(
        target=health_server,
        daemon=True
    ).start()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(callback)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    app.add_error_handler(error_handler)

    print("Limon BD Earning Bot চলছে...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
