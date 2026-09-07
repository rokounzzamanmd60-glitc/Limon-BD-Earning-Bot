import os, sqlite3, secrets, asyncio
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

DB='limon_bd.db'
CONFIG='config.txt'
STATE={}

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def setting(k,d=''):
    c=conn(); r=c.execute('select value from settings where key=?',(k,)).fetchone(); c.close(); return r['value'] if r else d

def set_setting(k,v):
    c=conn(); c.execute('insert into settings(key,value) values(?,?) on conflict(key) do update set value=excluded.value',(k,str(v))); c.commit(); c.close()

def init():
    c=conn(); q=c.execute
    q('create table if not exists settings(key text primary key,value text)')
    q('create table if not exists users(id integer primary key autoincrement,tg_id integer unique,name text,username text,balance real default 0,ref text unique,referred_by integer,ref_paid integer default 0,created_at text)')
    q('create table if not exists tasks(id integer primary key autoincrement,title text,description text,reward real,link text,active integer default 1,created_at text)')
    q('create table if not exists submissions(id integer primary key autoincrement,user_id integer,task_id integer,proof text,reward real,status text default "pending",created_at text,reviewed_at text)')
    q('create table if not exists deposits(id integer primary key autoincrement,user_id integer,method text,amount real,trx text,status text default "pending",created_at text,reviewed_at text)')
    q('create table if not exists withdrawals(id integer primary key autoincrement,user_id integer,method text,number text,amount real,status text default "pending",created_at text,reviewed_at text)')
    q('create table if not exists notifications(id integer primary key autoincrement,user_id integer,message text,created_at text,is_read integer default 0)')
    for k,v in {'bkash':'017XXXXXXXX','nagad':'019XXXXXXXX','ref_reward':'2','min_withdraw':'50'}.items(): q('insert or ignore into settings values(?,?)',(k,v))
    c.commit(); c.close()

def load_config():
    # Render/Web Service uses Environment Variables instead of interactive input.
    token=os.getenv('BOT_TOKEN','').strip()
    aid=os.getenv('ADMIN_ID','').strip()
    pwd=os.getenv('ADMIN_PASSWORD','12345').strip() or '12345'
    if token and aid:
        try:
            return token, int(aid), pwd
        except ValueError:
            raise RuntimeError('ADMIN_ID must be a numeric Telegram user ID.')

    # Optional local fallback: config.txt can still be used when running on a PC/mobile terminal.
    if os.path.exists(CONFIG):
        a=open(CONFIG,encoding='utf-8').read().splitlines()
        if len(a) >= 2 and a[0].strip() and a[1].strip():
            return a[0].strip(), int(a[1].strip()), (a[2].strip() if len(a) >= 3 and a[2].strip() else '12345')

    raise RuntimeError('BOT_TOKEN and ADMIN_ID environment variables are required on Render.')

TOKEN,ADMIN,PASSWORD=load_config()

def user(tg):
    c=conn(); r=c.execute('select * from users where tg_id=?',(tg.id,)).fetchone()
    if not r:
        ref=secrets.token_hex(4).upper(); c.execute('insert into users(tg_id,name,username,ref,created_at) values(?,?,?,?,?)',(tg.id,tg.full_name,tg.username or '',ref,now())); c.commit(); r=c.execute('select * from users where tg_id=?',(tg.id,)).fetchone()
    else: c.execute('update users set name=?,username=? where tg_id=?',(tg.full_name,tg.username or '',tg.id)); c.commit()
    c.close(); return r

def menu(uid):
    rows=[['🏠 Home','📋 Tasks'],['📤 Submit','💳 Deposit'],['💸 Withdraw','👥 Referral'],['📜 History','👤 Profile']]
    if uid==ADMIN: rows.append(['🔐 Admin'])
    return ReplyKeyboardMarkup(rows,resize_keyboard=True,is_persistent=True)

def amenu(): return ReplyKeyboardMarkup([['➕ Add Task','📋 Manage Tasks'],['📥 Submissions','💳 Deposits'],['💸 Withdrawals','⚙️ Settings'],['👥 Users','🏠 Home']],resize_keyboard=True,is_persistent=True)
def cancel_menu(): return ReplyKeyboardMarkup([['❌ Cancel']], resize_keyboard=True, is_persistent=True)
def method_menu(): return ReplyKeyboardMarkup([['bKash','Nagad'],['❌ Cancel']], resize_keyboard=True, is_persistent=True)
def clear(uid): STATE.pop(uid,None)

async def start(u,ct):
    x=u.effective_user; user(x)
    if ct.args and ct.args[0].startswith('ref_'):
        code=ct.args[0][4:]; c=conn(); me=c.execute('select * from users where tg_id=?',(x.id,)).fetchone(); rr=c.execute('select * from users where ref=?',(code,)).fetchone()
        if rr and rr['tg_id']!=x.id and me['referred_by'] is None:
            rew=float(setting('ref_reward','2')); c.execute('update users set referred_by=? where tg_id=?',(rr['tg_id'],x.id)); c.execute('update users set balance=balance+? where tg_id=?',(rew,rr['tg_id'])); c.execute('insert into notifications(user_id,message,created_at) values(?,?,?)',(rr['tg_id'],f'🎉 Referral Bonus +৳{rew:.2f}',now())); c.commit()
        c.close()
    clear(x.id); r=user(x)
    await u.message.reply_text(f'🎉 *Limon BD Earning*\n\n👋 স্বাগতম {x.full_name}!\n\n💰 Balance: ৳{r["balance"]:.2f}\n📋 Unlimited Tasks\n\nMenu থেকে Option নির্বাচন করুন।',parse_mode='Markdown',reply_markup=menu(x.id))

async def home(u,ct): clear(u.effective_user.id); r=user(u.effective_user); await u.message.reply_text(f'🏠 *Home*\n\n💰 Balance: ৳{r["balance"]:.2f}',parse_mode='Markdown',reply_markup=menu(u.effective_user.id))
async def profile(u,ct): clear(u.effective_user.id); r=user(u.effective_user); await u.message.reply_text(f'👤 *Profile*\n\n🆔 `{r["tg_id"]}`\n👤 {r["name"]}\n💰 ৳{r["balance"]:.2f}\n🎁 `{r["ref"]}`\n📅 {r["created_at"]}',parse_mode='Markdown',reply_markup=menu(u.effective_user.id))
async def referral(u,ct):
    clear(u.effective_user.id); r=user(u.effective_user); b=(await ct.bot.get_me()).username; c=conn(); n=c.execute('select count(*) c from users where referred_by=?',(r['tg_id'],)).fetchone()['c']; c.close(); link=f'https://t.me/{b}?start=ref_{r["ref"]}'; await u.message.reply_text(f'👥 *Referral*\n\n🎁 প্রতি Referral: ৳{float(setting("ref_reward","2")):.2f}\n👥 Total: {n}\n\n🔗 `{link}`',parse_mode='Markdown',reply_markup=menu(r['tg_id']))

async def tasks(u,ct):
    clear(u.effective_user.id); c=conn(); rows=c.execute('select * from tasks where active=1 order by id desc').fetchall(); c.close()
    if not rows: await u.message.reply_text('📋 এখন কোনো Task নেই।',reply_markup=menu(u.effective_user.id)); return
    await u.message.reply_text(f'📋 মোট {len(rows)}টি Task আছে।')
    for t in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('📤 Submit Proof',callback_data=f'sub:{t["id"]}')]])
        await u.message.reply_text(f'📌 *#{t["id"]} {t["title"]}*\n\n{t["description"]}\n\n💰 Reward: ৳{t["reward"]:.2f}\n🔗 {t["link"] or "নেই"}',parse_mode='Markdown',reply_markup=kb)

async def add_task_flow(u,ct,s):
    uid=u.effective_user.id; text=u.message.text
    step=s['step']; d=s['data']
    if step==1: d['title']=text; s['step']=2; await u.message.reply_text('Task Description লিখুন:',reply_markup=cancel_menu())
    elif step==2: d['description']=text; s['step']=3; await u.message.reply_text('Reward কত টাকা? শুধু সংখ্যা লিখুন:',reply_markup=cancel_menu())
    elif step==3:
        try: d['reward']=float(text); s['step']=4; await u.message.reply_text('Task Link দিন (না থাকলে - লিখুন):',reply_markup=cancel_menu())
        except: await u.message.reply_text('❌ Reward শুধু সংখ্যা হবে। যেমন 10')
    else:
        d['link']='' if text=='-' else text; c=conn(); c.execute('insert into tasks(title,description,reward,link,created_at) values(?,?,?,?,?)',(d['title'],d['description'],d['reward'],d['link'],now())); c.commit(); c.close(); clear(uid); await u.message.reply_text('✅ Task Added!',reply_markup=amenu())

async def submit_flow(u,ct,s):
    uid=u.effective_user.id; tid=s['task']; proof=u.message.text
    c=conn(); t=c.execute('select * from tasks where id=? and active=1',(tid,)).fetchone();
    if not t: c.close(); clear(uid); await u.message.reply_text('❌ Task পাওয়া যায়নি.'); return
    old=c.execute('select id from submissions where user_id=? and task_id=? and status="pending"',(uid,tid)).fetchone()
    if old: c.close(); clear(uid); await u.message.reply_text('⏳ এই Task-এর একটি submission already pending.'); return
    c.execute('insert into submissions(user_id,task_id,proof,reward,created_at) values(?,?,?,?,?)',(uid,tid,proof,t['reward'],now())); sid=c.lastrowid; c.commit(); c.close(); clear(uid)
    kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve',callback_data=f'sa:{sid}'),InlineKeyboardButton('❌ Reject',callback_data=f'sr:{sid}')]])
    await ct.bot.send_message(ADMIN,f'📥 *New Task Submission*\n\n🆔 Submission: {sid}\n👤 User: {uid}\n📌 Task: #{tid} {t["title"]}\n💰 Reward: ৳{t["reward"]:.2f}\n\n📝 Proof:\n{proof}',parse_mode='Markdown',reply_markup=kb)
    await u.message.reply_text('✅ Proof Admin-এর কাছে পাঠানো হয়েছে। Approval-এর জন্য অপেক্ষা করুন।',reply_markup=menu(uid))

async def deposit_flow(u,ct,s):
    uid=u.effective_user.id; text=u.message.text.strip(); step=s['step']; d=s['data']
    if step==1:
        if text not in ('bKash','Nagad'):
            await u.message.reply_text('নিচের bKash অথবা Nagad বাটনে চাপুন।',reply_markup=method_menu()); return
        d['method']=text; s['step']=2
        num=setting(text.lower(),'')
        await u.message.reply_text(f'💳 {text} Number: {num}\n\nকত টাকা Deposit করবেন?',reply_markup=cancel_menu())
    elif step==2:
        try:
            amount=float(text)
            if amount<=0: raise ValueError
            d['amount']=amount; s['step']=3
            await u.message.reply_text('Transaction ID (TrxID) লিখুন:',reply_markup=cancel_menu())
        except ValueError:
            await u.message.reply_text('❌ সঠিক Amount দিন।',reply_markup=cancel_menu())
    else:
        d['trx']=text; c=conn(); c.execute('insert into deposits(user_id,method,amount,trx,created_at) values(?,?,?,?,?)',(uid,d['method'],d['amount'],d['trx'],now())); did=c.lastrowid; c.commit(); c.close(); clear(uid)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve',callback_data=f'da:{did}'),InlineKeyboardButton('❌ Reject',callback_data=f'dr:{did}')]])
        await ct.bot.send_message(ADMIN,f'💳 *New Deposit*\n\nID: {did}\nUser: {uid}\nMethod: {d["method"]}\nAmount: ৳{d["amount"]:.2f}\nTrxID: `{d["trx"]}`',parse_mode='Markdown',reply_markup=kb)
        await u.message.reply_text('✅ Deposit request পাঠানো হয়েছে।',reply_markup=menu(uid))

async def withdraw_flow(u,ct,s):
    uid=u.effective_user.id; text=u.message.text.strip(); step=s['step']; d=s['data']
    if step==1:
        if text not in ('bKash','Nagad'):
            await u.message.reply_text('নিচের bKash অথবা Nagad বাটনে চাপুন।',reply_markup=method_menu()); return
        d['method']=text; s['step']=2
        await u.message.reply_text(f'{text} Number লিখুন:',reply_markup=cancel_menu())
    elif step==2:
        d['number']=text; s['step']=3
        await u.message.reply_text(f'Minimum Withdraw: ৳{setting("min_withdraw","50")}\n\nAmount লিখুন:',reply_markup=cancel_menu())
    else:
        try:
            amount=float(text); minimum=float(setting('min_withdraw','50'))
            if amount<minimum: await u.message.reply_text(f'❌ Minimum Withdraw ৳{minimum:.2f}',reply_markup=cancel_menu()); return
            if amount<=0: raise ValueError
        except ValueError:
            await u.message.reply_text('❌ সঠিক Amount দিন।',reply_markup=cancel_menu()); return
        c=conn(); r=c.execute('select balance from users where tg_id=?',(uid,)).fetchone()
        if not r or r['balance']<amount: c.close(); await u.message.reply_text('❌ আপনার Balance যথেষ্ট নয়।',reply_markup=cancel_menu()); return
        c.execute('update users set balance=balance-? where tg_id=?',(amount,uid))
        c.execute('insert into withdrawals(user_id,method,number,amount,created_at) values(?,?,?,?,?)',(uid,d['method'],d['number'],amount,now())); wid=c.lastrowid; c.commit(); c.close(); clear(uid)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve',callback_data=f'wa:{wid}'),InlineKeyboardButton('❌ Reject',callback_data=f'wr:{wid}')]])
        await ct.bot.send_message(ADMIN,f'💸 *New Withdrawal*\n\nID: {wid}\nUser: {uid}\nMethod: {d["method"]}\nNumber: {d["number"]}\nAmount: ৳{amount:.2f}',parse_mode='Markdown',reply_markup=kb)
        await u.message.reply_text('✅ Withdrawal request পাঠানো হয়েছে। Balance থেকে টাকা reserve করা হয়েছে।',reply_markup=menu(uid))

async def history(u,ct):
    uid=u.effective_user.id; clear(uid); c=conn(); a=c.execute('select * from submissions where user_id=? order by id desc limit 10',(uid,)).fetchall(); d=c.execute('select * from deposits where user_id=? order by id desc limit 10',(uid,)).fetchall(); w=c.execute('select * from withdrawals where user_id=? order by id desc limit 10',(uid,)).fetchall(); c.close(); out='📜 *History*\n\n📋 Submissions:\n'+('\n'.join(f'#{x["id"]} Task#{x["task_id"]} — {x["status"]} — ৳{x["reward"]:.2f}' for x in a) or 'None')+'\n\n💳 Deposits:\n'+('\n'.join(f'#{x["id"]} {x["method"]} ৳{x["amount"]:.2f} — {x["status"]}' for x in d) or 'None')+'\n\n💸 Withdrawals:\n'+('\n'.join(f'#{x["id"]} {x["method"]} ৳{x["amount"]:.2f} — {x["status"]}' for x in w) or 'None'); await u.message.reply_text(out,parse_mode='Markdown',reply_markup=menu(uid))

async def admin_menu(u,ct):
    if u.effective_user.id!=ADMIN: await u.message.reply_text('❌ Access Denied'); return
    clear(ADMIN); STATE[ADMIN]={'flow':'adminpass'}; await u.message.reply_text('🔐 Admin Password লিখুন:')

async def admin_manage(u,ct):
    c=conn(); rows=c.execute('select * from tasks order by id desc').fetchall(); c.close();
    if not rows: await u.message.reply_text('📋 কোনো Task নেই।',reply_markup=amenu()); return
    for t in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton('🗑 Delete',callback_data=f'tdel:{t["id"]}')]])
        await u.message.reply_text(f'#{t["id"]} {t["title"]}\n💰 ৳{t["reward"]:.2f}\n🔗 {t["link"] or "-"}',reply_markup=kb)

async def admin_list(u,ct,kind):
    table={'sub':'submissions','dep':'deposits','with':'withdrawals'}[kind]; c=conn(); rows=c.execute(f'select * from {table} where status="pending" order by id desc limit 30').fetchall(); c.close()
    if not rows: await u.message.reply_text('✅ কোনো Pending request নেই।',reply_markup=amenu()); return
    for r in rows:
        if kind=='sub':
            txt=f'📥 Submission #{r["id"]}\nUser: {r["user_id"]}\nTask: #{r["task_id"]}\nReward: ৳{r["reward"]}\nProof: {r["proof"]}'; cb=f'sa:{r["id"]}'; rb=f'sr:{r["id"]}'
        elif kind=='dep': txt=f'💳 Deposit #{r["id"]}\nUser: {r["user_id"]}\n{r["method"]}\n৳{r["amount"]}\nTrx: {r["trx"]}'; cb=f'da:{r["id"]}'; rb=f'dr:{r["id"]}'
        else: txt=f'💸 Withdrawal #{r["id"]}\nUser: {r["user_id"]}\n{r["method"]}\n{r["number"]}\n৳{r["amount"]}'; cb=f'wa:{r["id"]}'; rb=f'wr:{r["id"]}'
        await u.message.reply_text(txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Approve',callback_data=cb),InlineKeyboardButton('❌ Reject',callback_data=rb)]]))

async def admin_settings(u,ct):
    STATE[ADMIN]={'flow':'settings','step':1}
    kb=ReplyKeyboardMarkup([['💳 bKash','💳 Nagad'],['🎁 Referral','💸 Min Withdraw'],['❌ Cancel']],resize_keyboard=True,is_persistent=True)
    await u.message.reply_text(f'⚙️ Settings\n\nbKash: {setting("bkash")}\nNagad: {setting("nagad")}\nReferral: ৳{setting("ref_reward")}\nMinimum Withdraw: ৳{setting("min_withdraw") }\n\nযেটা পরিবর্তন করবেন সেটার বাটনে চাপুন।',reply_markup=kb)
async def settings_flow(u,ct,s):
    if s['step']==1:
        keymap={'💳 bKash':'bkash','💳 Nagad':'nagad','🎁 Referral':'ref_reward','💸 Min Withdraw':'min_withdraw'}
        key=keymap.get(u.message.text)
        if not key:
            await u.message.reply_text('নিচের একটি Settings বাটনে চাপুন।')
            return
        s['key']=key; s['step']=2; await u.message.reply_text('নতুন Value লিখুন:',reply_markup=cancel_menu())
    else:
        set_setting(s['key'],u.message.text); clear(ADMIN); await u.message.reply_text('✅ Setting Updated.',reply_markup=amenu())

async def admin_users(u,ct):
    c=conn(); n=c.execute('select count(*) c from users').fetchone()['c']; rows=c.execute('select tg_id,name,balance from users order by id desc limit 20').fetchall(); c.close(); txt=f'👥 Total Users: {n}\n\n'+'\n'.join(f'{r["tg_id"]} | {r["name"]} | ৳{r["balance"]:.2f}' for r in rows); await u.message.reply_text(txt,reply_markup=amenu())

async def callback(u,ct):
    q=u.callback_query; await q.answer(); uid=q.from_user.id; data=q.data
    if data.startswith('sub:'):
        tid=int(data.split(':')[1]); STATE[uid]={'flow':'submit','task':tid}; await q.message.reply_text('📤 Proof পাঠান (Screenshot নয় হলে Link/লিখিত Proof দিন):',reply_markup=cancel_menu()); return
    if uid!=ADMIN: return
    action,id_=data.split(':'); rid=int(id_); c=conn()
    try:
        c.execute('begin immediate')
        if action in ('sa','sr'):
            r=c.execute('select * from submissions where id=? and status="pending"',(rid,)).fetchone()
            if not r: raise ValueError('already')
            status='approved' if action=='sa' else 'rejected'; c.execute('update submissions set status=?,reviewed_at=? where id=?',(status,now(),rid))
            if action=='sa': c.execute('update users set balance=balance+? where tg_id=?',(r['reward'],r['user_id']))
            msg=('✅ Task Approved!\n💰 Reward +৳%.2f'%r['reward']) if action=='sa' else '❌ Task Submission Rejected.'
        elif action in ('da','dr'):
            r=c.execute('select * from deposits where id=? and status="pending"',(rid,)).fetchone()
            if not r: raise ValueError('already')
            status='approved' if action=='da' else 'rejected'; c.execute('update deposits set status=?,reviewed_at=? where id=?',(status,now(),rid))
            if action=='da': c.execute('update users set balance=balance+? where tg_id=?',(r['amount'],r['user_id']))
            msg=('✅ Deposit Approved!\n💰 +৳%.2f'%r['amount']) if action=='da' else '❌ Deposit Rejected.'
        elif action in ('wa','wr'):
            r=c.execute('select * from withdrawals where id=? and status="pending"',(rid,)).fetchone()
            if not r: raise ValueError('already')
            status='approved' if action=='wa' else 'rejected'; c.execute('update withdrawals set status=?,reviewed_at=? where id=?',(status,now(),rid))
            if action=='wr': c.execute('update users set balance=balance+? where tg_id=?',(r['amount'],r['user_id']))
            msg='✅ Withdrawal Approved.' if action=='wa' else '❌ Withdrawal Rejected.\n💰 Amount refunded to your Balance.'
        elif action=='tdel':
            c.execute('update tasks set active=0 where id=?',(rid,)); c.commit(); c.close(); await q.message.reply_text('🗑 Task deleted.'); return
        else: raise ValueError('bad')
        c.execute('insert into notifications(user_id,message,created_at) values(?,?,?)',(r['user_id'],msg,now())); c.commit(); target=r['user_id']
    except ValueError:
        c.rollback(); c.close(); await q.message.reply_text('⚠️ এই Request ইতিমধ্যে processed হয়েছে।'); return
    c.close(); await q.message.reply_text('✅ Done.')
    try: await ct.bot.send_message(target,msg)
    except: pass

async def text(u,ct):
    uid=u.effective_user.id; t=u.message.text.strip()
    if t=='❌ Cancel':
        clear(uid)
        await u.message.reply_text('❌ Cancel করা হয়েছে।',reply_markup=amenu() if uid==ADMIN else menu(uid))
        return
    if t=='🏠 Home': return await home(u,ct)
    if t=='📋 Tasks': return await tasks(u,ct)
    if t=='👤 Profile': return await profile(u,ct)
    if t=='👥 Referral': return await referral(u,ct)
    if t=='📜 History': return await history(u,ct)
    if t=='📤 Submit':
        await u.message.reply_text('📋 Tasks থেকে একটি Task-এর Submit Proof চাপুন।'); return
    if t=='💳 Deposit': STATE[uid]={'flow':'deposit','step':1,'data':{}}; await u.message.reply_text('Deposit Method নির্বাচন করুন:',reply_markup=method_menu()); return
    if t=='💸 Withdraw': STATE[uid]={'flow':'withdraw','step':1,'data':{}}; await u.message.reply_text('Withdraw Method নির্বাচন করুন:',reply_markup=method_menu()); return
    if t=='🔐 Admin': return await admin_menu(u,ct)
    if uid==ADMIN:
        if t=='➕ Add Task': STATE[uid]={'flow':'add','step':1,'data':{}}; await u.message.reply_text('Task Name লিখুন:',reply_markup=cancel_menu()); return
        if t=='📋 Manage Tasks': return await admin_manage(u,ct)
        if t=='📥 Submissions': return await admin_list(u,ct,'sub')
        if t=='💳 Deposits': return await admin_list(u,ct,'dep')
        if t=='💸 Withdrawals': return await admin_list(u,ct,'with')
        if t=='⚙️ Settings': return await admin_settings(u,ct)
        if t=='👥 Users': return await admin_users(u,ct)
    s=STATE.get(uid)
    if s:
        if s['flow']=='adminpass':
            if uid==ADMIN and t==PASSWORD: clear(uid); await u.message.reply_text('✅ Admin Login Successful',reply_markup=amenu())
            else: await u.message.reply_text('❌ Password ভুল।')
        elif s['flow']=='add': await add_task_flow(u,ct,s)
        elif s['flow']=='submit': await submit_flow(u,ct,s)
        elif s['flow']=='deposit': await deposit_flow(u,ct,s)
        elif s['flow']=='withdraw': await withdraw_flow(u,ct,s)
        elif s['flow']=='settings': await settings_flow(u,ct,s)
        return
    await u.message.reply_text('Menu থেকে Option নির্বাচন করুন।',reply_markup=menu(uid))

async def err(u,ct): print('ERROR:',ct.error)

def start_health_server():
    # Render Web Services require an HTTP port to stay available.
    # This tiny standard-library server avoids adding another package.
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    port=int(os.getenv('PORT','10000'))

    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body=b'Limon BD Earning Bot is running'
            self.send_response(200)
            self.send_header('Content-Type','text/plain; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, format, *args):
            return

    server=ThreadingHTTPServer(('0.0.0.0',port),HealthHandler)
    print(f'Health server listening on 0.0.0.0:{port}')
    server.serve_forever()

def main():
    init()
    import threading
    threading.Thread(target=start_health_server,daemon=True).start()
    app=Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start',start))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))
    app.add_error_handler(err)
    print('Limon BD Earning Bot চলছে...')
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__=='__main__': main()
