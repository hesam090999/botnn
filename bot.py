import logging
import sqlite3
import re
from datetime import datetime, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters
)
from telegram.constants import ChatMemberStatus

# ==================== تنظیمات ====================
BOT_TOKEN ="8680298065:AAGuiJ6R0std9vXOAVTb-B-QYrpSb9cnaXA"
OWNER_ID = 8076104332
DB_PATH = "bot_data.db"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== دیتابیس ====================
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    with db() as conn:
        c = conn.cursor()
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER,
                chat_id INTEGER,
                username TEXT,
                first_name TEXT,
                join_date TEXT,
                warnings INTEGER DEFAULT 0,
                verified INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS lock_targets (
                username TEXT PRIMARY KEY,
                type TEXT
            );
            CREATE TABLE IF NOT EXISTS sms (
                code TEXT,
                chat_id INTEGER,
                message TEXT,
                hours INTEGER,
                PRIMARY KEY (code, chat_id)
            );
            CREATE TABLE IF NOT EXISTS muted (
                user_id INTEGER,
                chat_id INTEGER,
                until TEXT,
                reason TEXT,
                PRIMARY KEY (user_id, chat_id)
            );
        ''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        conn.commit()

def is_admin(user_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
        return c.fetchone() is not None

def add_admin(user_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (user_id,))
        conn.commit()

def save_user(user, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM users WHERE user_id=? AND chat_id=?', (user.id, chat_id))
        exists = c.fetchone() is not None
        if not exists:
            c.execute('''INSERT INTO users (user_id, chat_id, username, first_name, join_date, warnings, verified)
                         VALUES (?,?,?,?,?,0,0)''',
                      (user.id, chat_id, user.username or '', user.first_name or '', datetime.now().isoformat()))
        else:
            c.execute('UPDATE users SET username=?, first_name=? WHERE user_id=? AND chat_id=?',
                      (user.username or '', user.first_name or '', user.id, chat_id))
        conn.commit()
        return not exists

def get_user(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('''SELECT user_id, username, first_name, join_date, warnings, verified
                     FROM users WHERE user_id=? AND chat_id=?''', (user_id, chat_id))
        return c.fetchone()

def set_verified(user_id, chat_id, v):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET verified=? WHERE user_id=? AND chat_id=?', (v, user_id, chat_id))
        conn.commit()

def inc_warnings(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET warnings = warnings + 1 WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        conn.commit()
        c.execute('SELECT warnings FROM users WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        return c.fetchone()[0]

def reset_warnings(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET warnings=0 WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        conn.commit()

def get_lock_targets():
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT username, type FROM lock_targets')
        return c.fetchall()

def add_lock_target(username, type_):
    with db() as conn:
        c = conn.cursor()
        try:
            c.execute('INSERT INTO lock_targets (username, type) VALUES (?,?)', (username, type_))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def remove_lock_target(username):
    with db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM lock_targets WHERE username=?', (username,))
        conn.commit()
        return c.rowcount > 0

def set_mute(user_id, chat_id, until, reason):
    with db() as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO muted (user_id, chat_id, until, reason) VALUES (?,?,?,?)',
                  (user_id, chat_id, until.isoformat(), reason))
        conn.commit()

def remove_mute(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM muted WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        conn.commit()

def get_mute(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT until, reason FROM muted WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        return c.fetchone()

def get_all_sms():
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT code, chat_id, message, hours FROM sms')
        return c.fetchall()

def add_sms(code, chat_id, message, hours):
    with db() as conn:
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO sms (code, chat_id, message, hours) VALUES (?,?,?,?)',
                  (code, chat_id, message, hours))
        conn.commit()

def remove_sms(code, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM sms WHERE code=? AND chat_id=?', (code, chat_id))
        conn.commit()
        return c.rowcount > 0

# ==================== ابزار ====================
def to_en_digits(text):
    fa = '۰۱۲۳۴۵۶۷۸۹'
    ar = '٠١٢٣٤٥٦٧٨٩'
    en = '0123456789'
    for i in range(10):
        text = text.replace(fa[i], en[i]).replace(ar[i], en[i])
    return text

async def restrict_user(bot, chat_id, user_id, seconds, reason):
    until = datetime.now() + timedelta(seconds=seconds)
    perms = ChatPermissions(can_send_messages=False)
    try:
        await bot.restrict_chat_member(chat_id, user_id, permissions=perms, until_date=until)
        set_mute(user_id, chat_id, until, reason)
        return True
    except Exception as e:
        logger.error(f"restrict failed: {e}")
        return False

async def unrestrict_user(bot, chat_id, user_id):
    perms = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_send_polls=True,
        can_invite_users=True,
    )
    try:
        await bot.restrict_chat_member(chat_id, user_id, permissions=perms)
        remove_mute(user_id, chat_id)
        return True
    except Exception as e:
        logger.error(f"unrestrict failed: {e}")
        return False

async def check_membership(bot, user_id):
    targets = get_lock_targets()
    if not targets:
        return True, []
    not_joined = []
    for username, _ in targets:
        try:
            m = await bot.get_chat_member(username, user_id)
            if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                not_joined.append(username)
        except Exception as e:
            logger.error(f"membership check for {username} failed: {e}")
            not_joined.append(username)
    return len(not_joined) == 0, not_joined

def build_lock_list(not_joined=None):
    targets = get_lock_targets()
    if not targets:
        return ""
    lines = []
    for username, type_ in targets:
        icon = "📢" if type_ == "channel" else "👥"
        lines.append(f"{icon} {username}")
    return "\n".join(lines)

# ==================== خوش‌آمدگویی ====================
async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cm = update.chat_member
    if not cm:
        return
    old = cm.old_chat_member.status
    new = cm.new_chat_member.status
    if old in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED) and \
       new in (ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED):
        user = cm.new_chat_member.user
        chat_id = cm.chat.id
        if user.is_bot:
            return

        is_new = save_user(user, chat_id)
        name = user.first_name or "کاربر"

        targets = get_lock_targets()
        if targets:
            # کاربر رو سکوت کن تا جوین شه
            await restrict_user(context.bot, chat_id, user.id, 60 * 60 * 24 * 365, "pending_verification")
            set_verified(user.id, chat_id, 0)
            lock_list = build_lock_list()
            text = (
                f"<b>{name}</b> به گروه خوش آمدید.\n\n"
                f"برای ارسال پیام و استفاده از ربات‌های رایگان، لطفاً در گروه‌ها و کانال‌های زیر عضو شوید:\n\n"
                f"{lock_list}\n\n"
                f"پس از عضویت، امکان ارسال پیام برای شما فعال می‌شود."
            )
        else:
            text = f"<b>{name}</b> به گروه خوش آمدید."
            set_verified(user.id, chat_id, 1)

        try:
            await context.bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"welcome send failed: {e}")

# ==================== بررسی عضویت دوره‌ای ====================
async def check_pending(context: ContextTypes.DEFAULT_TYPE):
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT user_id, chat_id FROM users WHERE verified=0')
        pending = c.fetchall()

    for user_id, chat_id in pending:
        ok, _ = await check_membership(context.bot, user_id)
        if ok:
            await unrestrict_user(context.bot, chat_id, user_id)
            set_verified(user_id, chat_id, 1)
            try:
                await context.bot.send_message(
                    chat_id,
                    f"عضویت شما تأیید شد. اکنون می‌توانید پیام ارسال کنید."
                )
            except Exception:
                pass

# ==================== دستور /user ====================
async def user_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("این دستور را باید روی پیام کاربر ریپلای کنید.")
        return
    if not is_admin(update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    chat_id = msg.chat_id
    row = get_user(target.id, chat_id)

    if not row:
        # اگر در دیتابیس نبود، همین حالا ذخیره کن
        save_user(target, chat_id)
        row = get_user(target.id, chat_id)

    user_id, username, first_name, join_date, warnings, verified = row

    try:
        jd = datetime.fromisoformat(join_date).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        jd = join_date or "نامشخص"

    mute = get_mute(target.id, chat_id)
    if mute:
        until = datetime.fromisoformat(mute[0]).strftime("%Y-%m-%d %H:%M")
        status = f"محدود تا {until} (دلیل: {mute[1]})"
    elif verified:
        status = "فعال"
    else:
        status = "در انتظار تأیید عضویت"

    text = (
        f"📋 <b>مشخصات کاربر</b>\n\n"
        f"👤 نام: {first_name or '—'}\n"
        f"🔗 یوزرنیم: {'@' + username if username else 'ندارد'}\n"
        f"🆔 یوزر آیدی: <code>{user_id}</code>\n"
        f"📅 تاریخ ورود: {jd}\n"
        f"⚠️ اخطارها: {warnings}\n"
        f"🔒 وضعیت: {status}"
    )
    await msg.reply_text(text, parse_mode="HTML")

# ==================== دستورات ادمین با ریپلای ====================
async def admin_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.reply_to_message:
        return
    if not is_admin(update.effective_user.id):
        return

    cmd = msg.text.strip()
    target = msg.reply_to_message.from_user
    chat_id = msg.chat_id
    name = target.first_name or "کاربر"

    if cmd in ("اخطار",):
        w = inc_warnings(target.id, chat_id)
        if w >= 3:
            reset_warnings(target.id, chat_id)
            await restrict_user(context.bot, chat_id, target.id, 300, "اخطار سوم")
            await msg.reply_text(
                f"کاربر {name} به دلیل دریافت سه اخطار، به مدت ۵ دقیقه از ارسال پیام محروم شد."
            )
        else:
            await restrict_user(context.bot, chat_id, target.id, 60, "اخطار")
            await msg.reply_text(
                f"کاربر {name} به دلیل نقض قوانین، به مدت ۱ دقیقه از ارسال پیام محروم شد.\n"
                f"تعداد اخطارهای فعلی: {w} از ۳"
            )

    elif cmd == "سکوت":
        await restrict_user(context.bot, chat_id, target.id, 300, "سکوت")
        await msg.reply_text(f"کاربر {name} به مدت ۵ دقیقه از ارسال پیام محروم شد.")

    elif cmd == "ببندش":
        await restrict_user(context.bot, chat_id, target.id, 86400, "بسته یک‌روزه")
        await msg.reply_text(f"کاربر {name} به مدت یک روز از ارسال پیام محروم شد.")

    elif cmd in ("ازاد", "آزاد"):
        reset_warnings(target.id, chat_id)
        set_verified(target.id, chat_id, 1)
        await unrestrict_user(context.bot, chat_id, target.id)
        await msg.reply_text(f"کاربر {name} از محدودیت خارج شد و از این پس می‌تواند پیام ارسال کند.")

# ==================== مدیریت ادمین‌ها ====================
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("فقط مالک ربات می‌تواند ادمین اضافه کند.")
        return
    if not context.args:
        await msg.reply_text("روش استفاده: /addadmin 123456789")
        return
    try:
        uid = int(to_en_digits(context.args[0]))
    except ValueError:
        await msg.reply_text("آیدی عددی معتبر نیست.")
        return
    add_admin(uid)
    await msg.reply_text(f"کاربر با آیدی {uid} به عنوان ادمین اضافه شد.")

# ==================== مدیریت کانال/گروه اجباری ====================
async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش استفاده: /addchannel @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if add_lock_target(u, "channel"):
        await update.message.reply_text(f"کانال {u} به لیست عضویت اجباری اضافه شد.")
    else:
        await update.message.reply_text(f"کانال {u} قبلاً اضافه شده است.")

async def del_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش استفاده: /delchannel @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if remove_lock_target(u):
        await update.message.reply_text(f"کانال {u} از لیست حذف شد.")
    else:
        await update.message.reply_text(f"کانال {u} در لیست نبود.")

async def add_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش استفاده: /addgroup @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if add_lock_target(u, "group"):
        await update.message.reply_text(f"گروه {u} به لیست عضویت اجباری اضافه شد.")
    else:
        await update.message.reply_text(f"گروه {u} قبلاً اضافه شده است.")

async def del_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش استفاده: /delgroup @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if remove_lock_target(u):
        await update.message.reply_text(f"گروه {u} از لیست حذف شد.")
    else:
        await update.message.reply_text(f"گروه {u} در لیست نبود.")

async def list_lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    targets = get_lock_targets()
    if not targets:
        await update.message.reply_text("هیچ کانال یا گروه اجباری ثبت نشده است.")
        return
    lines = ["📌 لیست عضویت‌های اجباری:\n"]
    for u, t in targets:
        icon = "📢" if t == "channel" else "👥"
        lines.append(f"{icon} {u}")
    await update.message.reply_text("\n".join(lines))

# ==================== پیام‌های زمان‌بندی‌شده (SMS) ====================
async def send_sms_job(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    code = data["code"]
    chat_id = data["chat_id"]
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT message FROM sms WHERE code=? AND chat_id=?', (code, chat_id))
        row = c.fetchone()
    if not row:
        return
    try:
        await context.bot.send_message(chat_id, row[0])
    except Exception as e:
        logger.error(f"sms send failed: {e}")

def schedule_sms(app, code, chat_id, hours):
    job_name = f"sms_{chat_id}_{code}"
    # حذف جاب قبلی اگه بود
    for j in app.job_queue.get_jobs_by_name(job_name):
        j.schedule_removal()
    app.job_queue.run_repeating(
        send_sms_job,
        interval=hours * 3600,
        first=10,
        name=job_name,
        data={"code": code, "chat_id": chat_id}
    )

async def add_sms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # این هندلر برای متن‌های /addsms.code.text.hours استفاده می‌شود
    msg = update.effective_message
    if not is_admin(update.effective_user.id):
        return
    parts = msg.text.split(".", 3)  # [/addsms, code, text, hours]
    # در واقع split با maxsplit=3 روی '،' ولی پیام ممکنه نقطه داشته باشه
    # بهتر: از راست پارس کنیم
    raw = msg.text
    # حذف /addsms.
    raw = re.sub(r'^/addsms\.', '', raw, flags=re.IGNORECASE)
    # جدا کردن آخرین بخش (ساعت)
    last_dot = raw.rfind(".")
    if last_dot == -1:
        await msg.reply_text("فرمت صحیح: /addsms.کد.متن.ساعت")
        return
    before, hours_str = raw[:last_dot], raw[last_dot+1:]
    # جدا کردن کد از متن
    first_dot = before.find(".")
    if first_dot == -1:
        await msg.reply_text("فرمت صحیح: /addsms.کد.متن.ساعت")
        return
    code = before[:first_dot].strip()
    text = before[first_dot+1:].strip()

    try:
        hours = int(to_en_digits(hours_str.strip()))
    except ValueError:
        await msg.reply_text("ساعت باید عددی بین ۱ تا ۲۴ باشد.")
        return

    if hours < 1 or hours > 24:
        await msg.reply_text("ساعت باید بین ۱ تا ۲۴ باشد.")
        return

    if not code or not text:
        await msg.reply_text("کد و متن پیام نمی‌تواند خالی باشد.")
        return

    add_sms(code, msg.chat_id, text, hours)
    schedule_sms(context.application, code, msg.chat_id, hours)
    await msg.reply_text(
        f"پیام زمان‌بندی‌شده با کد «{code}» ثبت شد و هر {hours} ساعت یک‌بار در این گروه ارسال می‌شود."
    )

async def rem_sms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not is_admin(update.effective_user.id):
        return
    raw = re.sub(r'^/remsms\.', '', msg.text, flags=re.IGNORECASE).strip()
    code = raw.split(".")[0].strip()
    if not code:
        await msg.reply_text("فرمت صحیح: /remsms.کد")
        return
    if remove_sms(code, msg.chat_id):
        job_name = f"sms_{msg.chat_id}_{code}"
        for j in context.application.job_queue.get_jobs_by_name(job_name):
            j.schedule_removal()
        await msg.reply_text(f"پیام با کد «{code}» حذف شد.")
    else:
        await msg.reply_text(f"پیامی با کد «{code}» در این گروه یافت نشد.")

# ==================== استارت ====================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ربات مدیریت گروه فعال است.\n"
        "برای دیدن دستورات، از ادمین‌ها بپرسید."
    )

async def post_init(app: Application):
    # زمان‌بندی مجدد پیام‌های ذخیره‌شده پس از ری‌استارت
    for code, chat_id, message, hours in get_all_sms():
        schedule_sms(app, code, chat_id, hours)
    # بررسی دوره‌ای کاربران در انتظار تأیید (هر ۳۰ ثانیه)
    app.job_queue.run_repeating(check_pending, interval=30, first=10)

# ==================== main ====================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ورود اعضای جدید
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # دستورات
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("user", user_info_cmd))
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("addchannel", add_channel_cmd))
    app.add_handler(CommandHandler("delchannel", del_channel_cmd))
    app.add_handler(CommandHandler("addgroup", add_group_cmd))
    app.add_handler(CommandHandler("delgroup", del_group_cmd))
    app.add_handler(CommandHandler("listlock", list_lock_cmd))

    # دستورات ادمینی که با ریپلای کار می‌کنند
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.Regex(r'^(اخطار|سکوت|ببندش|ازاد|آزاد)$') & filters.REPLY,
        admin_actions
    ))

    # SMS با فرمت نقطه‌ای (به‌عنوان متن، نه کامند)
    app.add_handler(MessageHandler(
        filters.Regex(r'^/addsms\.') & filters.ChatType.GROUPS,
        add_sms_cmd
    ))
    app.add_handler(MessageHandler(
        filters.Regex(r'^/remsms\.') & filters.ChatType.GROUPS,
        rem_sms_cmd
    ))

    print("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
