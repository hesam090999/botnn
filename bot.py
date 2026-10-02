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
BOT_TOKEN = "8680298065:AAGuiJ6R0std9vXOAVTb-B-QYrpSb9cnaXA"
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
                last_name TEXT,
                join_date TEXT,
                warnings INTEGER DEFAULT 0,
                warn_count INTEGER DEFAULT 0,
                mute_count INTEGER DEFAULT 0,
                ban_count INTEGER DEFAULT 0,
                total_warnings INTEGER DEFAULT 0,
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
        ''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        conn.commit()

def is_db_admin(user_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM admins WHERE user_id=?', (user_id,))
        return c.fetchone() is not None

async def is_admin(bot, chat_id, user_id):
    """بررسی ادمین: مالک یا ادمین دیتابیس یا ادمین واقعی گروه"""
    if user_id == OWNER_ID:
        return True
    if is_db_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            return True
    except Exception as e:
        logger.error(f"admin check failed: {e}")
    return False

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
            c.execute('''INSERT INTO users 
                         (user_id, chat_id, username, first_name, last_name, join_date, 
                          warnings, warn_count, mute_count, ban_count, total_warnings)
                         VALUES (?,?,?,?,?,?,0,0,0,0,0)''',
                      (user.id, chat_id, user.username or '', user.first_name or '',
                       user.last_name or '', datetime.now().isoformat()))
        else:
            c.execute('''UPDATE users SET username=?, first_name=?, last_name=?
                         WHERE user_id=? AND chat_id=?''',
                      (user.username or '', user.first_name or '',
                       user.last_name or '', user.id, chat_id))
        conn.commit()

def get_user(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('''SELECT user_id, username, first_name, last_name, join_date,
                            warnings, warn_count, mute_count, ban_count, total_warnings
                     FROM users WHERE user_id=? AND chat_id=?''', (user_id, chat_id))
        return c.fetchone()

def inc_warnings(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('''UPDATE users 
                     SET warnings = warnings + 1,
                         warn_count = warn_count + 1,
                         total_warnings = total_warnings + 1
                     WHERE user_id=? AND chat_id=?''', (user_id, chat_id))
        conn.commit()
        c.execute('SELECT warnings FROM users WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        r = c.fetchone()
        return r[0] if r else 0

def inc_mute(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET mute_count = mute_count + 1 WHERE user_id=? AND chat_id=?',
                  (user_id, chat_id))
        conn.commit()

def inc_ban(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET ban_count = ban_count + 1 WHERE user_id=? AND chat_id=?',
                  (user_id, chat_id))
        conn.commit()

def reset_warnings(user_id, chat_id):
    with db() as conn:
        c = conn.cursor()
        c.execute('UPDATE users SET warnings=0 WHERE user_id=? AND chat_id=?', (user_id, chat_id))
        conn.commit()

def clear_all_stats(user_id, chat_id):
    """پاک کردن همه آمار: اخطار، سکوت، بن"""
    with db() as conn:
        c = conn.cursor()
        c.execute('''UPDATE users 
                     SET warnings=0, warn_count=0, mute_count=0, ban_count=0, total_warnings=0
                     WHERE user_id=? AND chat_id=?''', (user_id, chat_id))
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

def mention(user):
    name = user.first_name or "کاربر"
    return f'<a href="tg://user?id={user.id}">{name}</a>'

async def restrict_user(bot, chat_id, user_id, seconds, reason):
    until = datetime.now() + timedelta(seconds=seconds)
    perms = ChatPermissions(can_send_messages=False)
    try:
        await bot.restrict_chat_member(chat_id, user_id, permissions=perms, until_date=until)
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
        return True
    except Exception as e:
        logger.error(f"unrestrict failed: {e}")
        return False

def build_lock_list():
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

        save_user(user, chat_id)
        user_mention = mention(user)

        targets = get_lock_targets()
        if targets:
            await restrict_user(context.bot, chat_id, user.id, 60 * 60 * 24 * 365, "pending")
            lock_list = build_lock_list()
            text = (
                f"👋 {user_mention} به گروه خوش آمدید.\n\n"
                f"⚠️ برای اینکه بتوانید پیام ارسال کنید و از ربات‌های رایگان استفاده کنید، "
                f"ابتدا باید در گروه‌ها و کانال‌های زیر عضو شوید:\n\n"
                f"{lock_list}\n\n"
                f"✅ پس از عضویت، به‌صورت خودکار امکان ارسال پیام برای شما فعال می‌شود."
            )
        else:
            text = f"👋 {user_mention} به گروه خوش آمدید."

        try:
            await context.bot.send_message(chat_id, text, parse_mode="HTML",
                                            disable_web_page_preview=True)
        except Exception as e:
            logger.error(f"welcome failed: {e}")

# ==================== نمایش مشخصات ====================
async def show_user_info(msg, target_user, chat_id):
    save_user(target_user, chat_id)
    row = get_user(target_user.id, chat_id)
    if not row:
        await msg.reply_text("خطا در خواندن اطلاعات.")
        return

    (uid, username, first_name, last_name, join_date,
     warnings, warn_count, mute_count, ban_count, total_warnings) = row

    try:
        jd = datetime.fromisoformat(join_date).strftime("%Y-%m-%d %H:%M")
    except:
        jd = join_date or "نامشخص"

    full_name = f"{first_name or ''} {last_name or ''}".strip() or "—"

    text = (
        f"📋 <b>مشخصات کاربر</b>\n\n"
        f"👤 نام کامل: {full_name}\n"
        f"🔗 یوزرنیم: {'@' + username if username else 'ندارد'}\n"
        f"🆔 یوزر آیدی: <code>{uid}</code>\n"
        f"📅 تاریخ ورود: {jd}\n\n"
        f"📊 <b>آمار محدودیت‌ها:</b>\n"
        f"⚠️ اخطارهای فعلی: <b>{warnings}</b> از ۳\n"
        f"📌 مجموع اخطارها: <b>{total_warnings}</b>\n"
        f"📝 دفعات اخطار: <b>{warn_count}</b>\n"
        f"🔇 دفعات سکوت: <b>{mute_count}</b>\n"
        f"🚫 دفعات بسته شدن: <b>{ban_count}</b>"
    )
    await msg.reply_text(text, parse_mode="HTML")

# ==================== پنل ====================
PANEL_TEXT = """🤖 <b>پنل مدیریت ربات</b>

📌 <b>دستورات با ریپلای روی پیام کاربر:</b>

🔹 <code>ایدی</code> — نمایش مشخصات و آمار کامل
🔹 <code>سکوت</code> — ۵ دقیقه سکوت
🔹 <code>اخطار</code> — ۱ دقیقه (۳ اخطار = ۵ دقیقه)
🔹 <code>ببندش</code> — ۲۴ ساعت سکوت
🔹 <code>ازاد</code> — آزادسازی کاربر
🔹 <code>پاک</code> — پاک کردن همه آمار و اخطارها

━━━━━━━━━━━━━━━

⚙️ <b>دستورات ادمین:</b>

👮 <code>/addadmin 123456789</code>
📢 <code>/addchannel @username</code>
👥 <code>/addgroup @username</code>
🗑 <code>/delchannel @username</code>
🗑 <code>/delgroup @username</code>
📋 <code>/listlock</code>

━━━━━━━━━━━━━━━

💬 <b>پیام‌های زمان‌بندی‌شده:</b>

➕ <code>/addsms.کد.متن.ساعت</code>
➖ <code>/remsms.کد</code>

━━━━━━━━━━━━━━━

📝 <b>نکات مهم:</b>
• همه دستورات فارسی با ریپلای روی پیام کاربر
• ربات باید توی گروه ادمین باشه
• دسترسی Restrict Members فعال باشه"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PANEL_TEXT, parse_mode="HTML")

# ==================== ایدی ====================
async def userid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return
    await show_user_info(msg, msg.reply_to_message.from_user, msg.chat_id)

# ==================== سکوت ====================
async def mute_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    save_user(target, msg.chat_id)
    target_mention = mention(target)

    ok = await restrict_user(context.bot, msg.chat_id, target.id, 300, "mute")
    if ok:
        inc_mute(target.id, msg.chat_id)
        await msg.reply_text(
            f"🔇 {target_mention} شما به دلیل نقض قوانین گروه، به مدت <b>۵ دقیقه</b> "
            f"از ارسال پیام محروم شدید.",
            parse_mode="HTML"
        )
    else:
        await msg.reply_text("خطا. مطمئن شوید ربات ادمین است و دسترسی Restrict Members دارد.")

# ==================== اخطار ====================
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    save_user(target, msg.chat_id)
    target_mention = mention(target)
    w = inc_warnings(target.id, msg.chat_id)

    if w >= 3:
        reset_warnings(target.id, msg.chat_id)
        await restrict_user(context.bot, msg.chat_id, target.id, 300, "warn3")
        await msg.reply_text(
            f"⚠️ {target_mention} شما <b>۳ اخطار</b> دریافت کرده‌اید و به مدت <b>۵ دقیقه</b> "
            f"از ارسال پیام محروم شدید.",
            parse_mode="HTML"
        )
    else:
        await restrict_user(context.bot, msg.chat_id, target.id, 60, "warn")
        await msg.reply_text(
            f"⚠️ {target_mention} شما یک اخطار دریافت کردید و به مدت <b>۱ دقیقه</b> "
            f"از ارسال پیام محروم شدید.\n"
            f"تعداد اخطارهای شما: <b>{w} از ۳</b>",
            parse_mode="HTML"
        )

# ==================== ببندش ====================
async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    save_user(target, msg.chat_id)
    target_mention = mention(target)

    ok = await restrict_user(context.bot, msg.chat_id, target.id, 86400, "ban")
    if ok:
        inc_ban(target.id, msg.chat_id)
        await msg.reply_text(
            f"🚫 {target_mention} شما به دلیل نقض جدی قوانین گروه، به مدت "
            f"<b>۲۴ ساعت (یک روز)</b> از ارسال پیام محروم شدید.",
            parse_mode="HTML"
        )
    else:
        await msg.reply_text("خطا. مطمئن شوید ربات ادمین است.")

# ==================== ازاد ====================
async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    target_mention = mention(target)
    reset_warnings(target.id, msg.chat_id)
    await unrestrict_user(context.bot, msg.chat_id, target.id)
    await msg.reply_text(
        f"✅ {target_mention} محدودیت شما برداشته شد و از این پس می‌توانید "
        f"در گروه پیام ارسال کنید.",
        parse_mode="HTML"
    )

# ==================== پاک (پاک کردن همه آمار) ====================
async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg.reply_to_message:
        await msg.reply_text("روی پیام کاربر ریپلای کنید.")
        return
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return

    target = msg.reply_to_message.from_user
    save_user(target, msg.chat_id)
    target_mention = mention(target)
    clear_all_stats(target.id, msg.chat_id)
    await msg.reply_text(
        f"🧹 {target_mention} تمام آمار و اخطارهای شما پاک شد.\n"
        f"از این پس با پرونده‌ای تمیز در گروه فعالیت می‌کنید.",
        parse_mode="HTML"
    )

# ==================== هندلر متن‌های فارسی ====================
async def persian_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """هر پیام متنی فارسی رو به تابع مربوطه می‌فرسته"""
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip()

    # لاگ برای دیباگ
    logger.info(f"📩 پیام دریافت شد: '{text}' از کاربر {update.effective_user.id}")

    # نقشه دستورات
    if text == "سکوت":
        await mute_cmd(update, context)
    elif text == "اخطار":
        await warn_cmd(update, context)
    elif text == "ببندش":
        await ban_cmd(update, context)
    elif text in ("ازاد", "آزاد"):
        await free_cmd(update, context)
    elif text == "ایدی":
        await userid_cmd(update, context)
    elif text == "پاک":
        await clear_cmd(update, context)

# ==================== مدیریت ادمین ====================
async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != OWNER_ID:
        await msg.reply_text("فقط مالک ربات.")
        return
    if not context.args:
        await msg.reply_text("روش: /addadmin 123456789")
        return
    try:
        uid = int(to_en_digits(context.args[0]))
    except ValueError:
        await msg.reply_text("آیدی معتبر نیست.")
        return
    add_admin(uid)
    await msg.reply_text(f"کاربر {uid} به عنوان ادمین اضافه شد.")

# ==================== کانال / گروه ====================
async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش: /addchannel @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if add_lock_target(u, "channel"):
        await update.message.reply_text(f"کانال {u} اضافه شد.")
    else:
        await update.message.reply_text(f"کانال {u} قبلاً اضافه شده.")

async def del_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش: /delchannel @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if remove_lock_target(u):
        await update.message.reply_text(f"کانال {u} حذف شد.")
    else:
        await update.message.reply_text(f"کانال {u} در لیست نبود.")

async def add_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش: /addgroup @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if add_lock_target(u, "group"):
        await update.message.reply_text(f"گروه {u} اضافه شد.")
    else:
        await update.message.reply_text(f"گروه {u} قبلاً اضافه شده.")

async def del_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("روش: /delgroup @username")
        return
    u = context.args[0]
    if not u.startswith("@"):
        u = "@" + u
    if remove_lock_target(u):
        await update.message.reply_text(f"گروه {u} حذف شد.")
    else:
        await update.message.reply_text(f"گروه {u} در لیست نبود.")

async def list_lock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(context.bot, update.effective_chat.id, update.effective_user.id):
        return
    targets = get_lock_targets()
    if not targets:
        await update.message.reply_text("هیچ اجباری ثبت نشده.")
        return
    lines = ["📌 لیست اجباری‌ها:\n"]
    for u, t in targets:
        icon = "📢" if t == "channel" else "👥"
        lines.append(f"{icon} {u}")
    await update.message.reply_text("\n".join(lines))

# ==================== SMS ====================
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
        logger.error(f"sms failed: {e}")

def schedule_sms(app, code, chat_id, hours):
    job_name = f"sms_{chat_id}_{code}"
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
    msg = update.effective_message
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return
    raw = re.sub(r'^/addsms\.', '', msg.text, flags=re.IGNORECASE)
    last_dot = raw.rfind(".")
    if last_dot == -1:
        await msg.reply_text("فرمت: /addsms.کد.متن.ساعت")
        return
    before, hours_str = raw[:last_dot], raw[last_dot+1:]
    first_dot = before.find(".")
    if first_dot == -1:
        await msg.reply_text("فرمت: /addsms.کد.متن.ساعت")
        return
    code = before[:first_dot].strip()
    text = before[first_dot+1:].strip()

    try:
        hours = int(to_en_digits(hours_str.strip()))
    except ValueError:
        await msg.reply_text("ساعت باید عدد بین ۱ تا ۲۴ باشد.")
        return

    if hours < 1 or hours > 24:
        await msg.reply_text("ساعت باید بین ۱ تا ۲۴ باشد.")
        return
    if not code or not text:
        await msg.reply_text("کد و متن خالی نباشد.")
        return

    add_sms(code, msg.chat_id, text, hours)
    schedule_sms(context.application, code, msg.chat_id, hours)
    await msg.reply_text(f"پیام با کد «{code}» ثبت شد. هر {hours} ساعت ارسال می‌شود.")

async def rem_sms_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_admin(context.bot, msg.chat_id, update.effective_user.id):
        return
    raw = re.sub(r'^/remsms\.', '', msg.text, flags=re.IGNORECASE).strip()
    code = raw.split(".")[0].strip()
    if not code:
        await msg.reply_text("فرمت: /remsms.کد")
        return
    if remove_sms(code, msg.chat_id):
        job_name = f"sms_{msg.chat_id}_{code}"
        for j in context.application.job_queue.get_jobs_by_name(job_name):
            j.schedule_removal()
        await msg.reply_text(f"پیام با کد «{code}» حذف شد.")
    else:
        await msg.reply_text(f"کد «{code}» یافت نشد.")

# ==================== بررسی دوره‌ای عضویت ====================
async def check_pending(context: ContextTypes.DEFAULT_TYPE):
    targets = get_lock_targets()
    if not targets:
        return
    with db() as conn:
        c = conn.cursor()
        c.execute('SELECT user_id, chat_id FROM users')
        users = c.fetchall()

    for user_id, chat_id in users:
        try:
            all_ok = True
            for username, _ in targets:
                try:
                    m = await context.bot.get_chat_member(username, user_id)
                    if m.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                        all_ok = False
                        break
                except:
                    all_ok = False
                    break
            if all_ok:
                await unrestrict_user(context.bot, chat_id, user_id)
        except:
            pass

# ==================== post init ====================
async def post_init(app: Application):
    for code, chat_id, message, hours in get_all_sms():
        schedule_sms(app, code, chat_id, hours)
    app.job_queue.run_repeating(check_pending, interval=60, first=30)

# ==================== main ====================
def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ورود اعضا
    app.add_handler(ChatMemberHandler(on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    # دستورات اسلش‌دار
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("panel", start_cmd))
    app.add_handler(CommandHandler("help", start_cmd))
    app.add_handler(CommandHandler("addadmin", add_admin_cmd))
    app.add_handler(CommandHandler("addchannel", add_channel_cmd))
    app.add_handler(CommandHandler("delchannel", del_channel_cmd))
    app.add_handler(CommandHandler("addgroup", add_group_cmd))
    app.add_handler(CommandHandler("delgroup", del_group_cmd))
    app.add_handler(CommandHandler("listlock", list_lock_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^/addsms\.'), add_sms_cmd))
    app.add_handler(MessageHandler(filters.Regex(r'^/remsms\.'), rem_sms_cmd))

    # ⭐ مهم: هندلر متن‌های فارسی (بدون اسلش)
    app.add_handler(MessageHandler(
        filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND,
        persian_text_handler
    ))

    print("ربات در حال اجراست...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
