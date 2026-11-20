# =========================
# ST STREAK BOT - REPAIRED
# PART 1: CORE + DATABASE
# =========================

import os
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ST")

TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "/data/streaks.sqlite3")
TZ = ZoneInfo("Asia/Kuwait")
PREFIX = "-"

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True
)

PHOTO_LOCKS = {}
ADMIN_LOCKS = {}

DEFAULT_CONFIG = {
    "streak_channel_id": 0,
    "divider_image_url": "",
    "divider_enabled": True,

    "dm_photo_enabled": True,
    "dm_photo_template": "تم تسجيل صورتك! ستريكك الحالي {streak} يوم 🔥",

    "dm_start_enabled": True,
    "dm_start_text": "بديت ستريك! استمر 🔥",

    "dm_rank_enabled": True,
    "dm_rank_template": "مبروك! وصلت رتبة {rank} بعد {days} يوم 🏆",

    "reaction_enabled": True,
    "default_emoji": "🔥",

    "shield_enabled": True,
    "default_shields": 1,

    "keep_old_rank_roles": True,
    "remove_rank_roles_on_break": False,

    "log_photo_channel_id": 0,
    "log_delete_channel_id": 0,
    "log_reaction_channel_id": 0,
    "log_dm_channel_id": 0,
    "log_shield_channel_id": 0,
    "log_admin_channel_id": 0,

    "log_photos_enabled": True,
    "log_deletes_enabled": True,
    "log_reactions_enabled": True,
    "log_dms_enabled": True,
    "log_shields_enabled": True,
    "log_admin_enabled": True,

    "customization_enabled": True,

    "ranks": [
        {"days": 1, "name": "Streak Start", "role_id": 0},
        {"days": 7, "name": "Streak Rookie", "role_id": 0},
        {"days": 14, "name": "Streak Keeper", "role_id": 0},
        {"days": 30, "name": "Streak Master", "role_id": 0},
        {"days": 100, "name": "Unstoppable", "role_id": 0},
        {"days": 365, "name": "Streak Legend", "role_id": 0},
    ]
}


# =========================
# DATABASE
# =========================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS members (
            user_id INTEGER PRIMARY KEY,
            current_streak INTEGER DEFAULT 0,
            longest_streak INTEGER DEFAULT 0,
            total_photos INTEGER DEFAULT 0,
            streak_photos INTEGER DEFAULT 0,
            last_photo_date TEXT,
            streak_start_date TEXT,
            shields INTEGER DEFAULT 0,
            personal_emoji TEXT,
            personal_title TEXT,
            show_title INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS processed_messages (
            message_id INTEGER PRIMARY KEY,
            processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS streak_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            start_date TEXT,
            end_date TEXT,
            days INTEGER,
            photos INTEGER,
            recorded_at TEXT
        );

        CREATE TABLE IF NOT EXISTS milestone_awards (
            user_id INTEGER,
            days INTEGER,
            awarded_at TEXT,
            PRIMARY KEY(user_id, days)
        );

        CREATE TABLE IF NOT EXISTS shield_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date_used TEXT,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS dm_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT,
            content TEXT,
            sent INTEGER,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            action TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS reaction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reactor_id INTEGER,
            owner_id INTEGER,
            message_id INTEGER,
            emoji TEXT,
            action TEXT,
            created_at TEXT
        );
        """)

        # إضافة الأعمدة الناقصة لقواعد البيانات القديمة
        cols = {
            row[1] for row in
            c.execute("PRAGMA table_info(members)").fetchall()
        }

        migrations = {
            "streak_start_date": "TEXT",
            "personal_emoji": "TEXT",
            "personal_title": "TEXT",
            "show_title": "INTEGER DEFAULT 1",
        }

        for column, definition in migrations.items():
            if column not in cols:
                c.execute(
                    f"ALTER TABLE members ADD COLUMN {column} {definition}"
                )

        for key, value in DEFAULT_CONFIG.items():
            c.execute(
                "INSERT OR IGNORE INTO config(key,value) VALUES(?,?)",
                (key, json.dumps(value, ensure_ascii=False))
            )


init_db()


def get_config(key):
    with db() as c:
        row = c.execute(
            "SELECT value FROM config WHERE key=?",
            (key,)
        ).fetchone()

    if not row:
        return DEFAULT_CONFIG.get(key)

    try:
        return json.loads(row["value"])
    except Exception:
        return DEFAULT_CONFIG.get(key)


def set_config(key, value):
    with db() as c:
        c.execute("""
            INSERT INTO config(key,value)
            VALUES(?,?)
            ON CONFLICT(key)
            DO UPDATE SET value=excluded.value
        """, (key, json.dumps(value, ensure_ascii=False)))


def get_member(uid):
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO members(user_id,shields) VALUES(?,?)",
            (uid, int(get_config("default_shields") or 0))
        )
        row = c.execute(
            "SELECT * FROM members WHERE user_id=?",
            (uid,)
        ).fetchone()

    return dict(row)


def update_member(uid, **fields):
    allowed = {
        "current_streak", "longest_streak",
        "total_photos", "streak_photos",
        "last_photo_date", "streak_start_date",
        "shields", "personal_emoji",
        "personal_title", "show_title"
    }

    fields = {
        k: v for k, v in fields.items()
        if k in allowed
    }

    if not fields:
        return

    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO members(user_id,shields) VALUES(?,?)",
            (uid, int(get_config("default_shields") or 0))
        )

        assignments = ", ".join(
            f"{key}=?" for key in fields
        )

        c.execute(
            f"UPDATE members SET {assignments} WHERE user_id=?",
            (*fields.values(), uid)
        )


# =========================
# TIME + HELPERS
# =========================

def today():
    return datetime.now(TZ).date()


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def safe_emoji(value=None):
    return str(value or get_config("default_emoji") or "🔥")


def member_emoji(uid):
    return safe_emoji(get_member(uid).get("personal_emoji"))


def effective_streak(member):
    streak = int(member.get("current_streak") or 0)
    last = member.get("last_photo_date")

    if streak <= 0 or not last:
        return 0

    try:
        gap = (today() - date.fromisoformat(last)).days
    except Exception:
        return 0

    if gap in (0, 1):
        return streak

    if (
        gap == 2
        and get_config("shield_enabled")
        and int(member.get("shields") or 0) > 0
    ):
        return streak

    return 0


def make_embed(title, description="", color=0x5865F2):
    e = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(TZ)
    )
    e.set_footer(text="ST • Streak System")
    return e


def get_channel(guild, channel_id):
    if not channel_id:
        return None
    return guild.get_channel(int(channel_id))


def is_admin(member):
    return (
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
    )
    # =========================
# LOGGING
# =========================

async def log_admin(guild, admin, action):
    with db() as c:
        c.execute(
            "INSERT INTO admin_logs(admin_id,action,created_at) VALUES(?,?,?)",
            (admin.id, str(action), now_iso())
        )

    if not get_config("log_admin_enabled"):
        return

    channel = get_channel(
        guild,
        get_config("log_admin_channel_id")
    )

    if channel:
        try:
            await channel.send(embed=make_embed(
                "🛡️ Admin Activity",
                f"**Admin:** {admin.mention}\n"
                f"**Action:** {action}"
            ))
        except Exception:
            logger.exception("Admin log failed")


async def log_dm(guild, user, kind, content, sent):
    with db() as c:
        c.execute(
            "INSERT INTO dm_logs(user_id,kind,content,sent,created_at) "
            "VALUES(?,?,?,?,?)",
            (user.id, kind, content, int(sent), now_iso())
        )

    if not get_config("log_dms_enabled"):
        return

    channel = get_channel(
        guild,
        get_config("log_dm_channel_id")
    )

    if channel:
        try:
            status = "وصل" if sent else "فشل"

            await channel.send(embed=make_embed(
                "📨 DM Log",
                f"**العضو:** {user.mention}\n"
                f"**النوع:** {kind}\n"
                f"**الحالة:** {status}\n"
                f"**المحتوى:**\n{content[:3000]}"
            ))
        except Exception:
            logger.exception("DM logging failed")


async def dm_user(guild, user, kind, content):
    sent = False

    try:
        await user.send(content)
        sent = True
    except (discord.Forbidden, discord.HTTPException):
        sent = False

    await log_dm(guild, user, kind, content, sent)
    return sent


# =========================
# RANKS
# =========================

async def check_ranks(guild, member, days):
    ranks = sorted(
        get_config("ranks") or [],
        key=lambda r: int(r.get("days", 0))
    )

    awarded_now = []

    for rank in ranks:
        milestone = int(rank.get("days", 0))

        if milestone <= 0 or days < milestone:
            continue

        with db() as c:
            exists = c.execute(
                "SELECT 1 FROM milestone_awards "
                "WHERE user_id=? AND days=?",
                (member.id, milestone)
            ).fetchone()

        if exists:
            continue

        role_id = int(rank.get("role_id") or 0)
        role = guild.get_role(role_id) if role_id else None

        if role and role not in member.roles:
            try:
                if not get_config("keep_old_rank_roles"):
                    old_roles = []

                    for old_rank in ranks:
                        old_id = int(old_rank.get("role_id") or 0)
                        old_role = guild.get_role(old_id)

                        if old_role and old_role in member.roles:
                            old_roles.append(old_role)

                    if old_roles:
                        await member.remove_roles(*old_roles)

                await member.add_roles(
                    role,
                    reason=f"ST streak milestone: {milestone}"
                )

            except discord.Forbidden:
                logger.warning("Cannot assign rank role")
            except discord.HTTPException:
                logger.exception("Rank assignment error")

        # تسجيل الرتبة مرة واحدة فقط
        with db() as c:
            c.execute(
                "INSERT OR IGNORE INTO milestone_awards"
                "(user_id,days,awarded_at) VALUES(?,?,?)",
                (member.id, milestone, now_iso())
            )

        awarded_now.append(rank)

        if get_config("dm_rank_enabled"):
            template = (
                get_config("dm_rank_template")
                or DEFAULT_CONFIG["dm_rank_template"]
            )

            try:
                message = template.format(
                    rank=rank.get("name", "Rank"),
                    days=milestone
                )
            except Exception:
                message = (
                    f"🏆 مبروك! وصلت رتبة "
                    f"{rank.get('name')} بعد {milestone} يوم."
                )

            await dm_user(
                guild,
                member,
                "rank",
                message
            )


# =========================
# STREAK PROCESSING
# =========================

async def record_photo(message, images):
    lock = PHOTO_LOCKS.setdefault(
        message.author.id,
        asyncio.Lock()
    )

    async with lock:
        await record_photo_locked(message, images)


async def record_photo_locked(message, images):
    uid = message.author.id
    today_date = today()
    today_string = today_date.isoformat()

    # منع تسجيل نفس الرسالة مرتين
    with db() as c:
        inserted = c.execute(
            "INSERT OR IGNORE INTO processed_messages"
            "(message_id,processed_at) VALUES(?,?)",
            (message.id, now_iso())
        ).rowcount

    if inserted == 0:
        return

    member_data = get_member(uid)

    last_date = member_data.get("last_photo_date")
    current = int(member_data.get("current_streak") or 0)
    streak_photos = int(member_data.get("streak_photos") or 0)
    shields = int(member_data.get("shields") or 0)

    new_streak = current
    new_photos = streak_photos + len(images)

    started = False
    shield_used = False

    try:
        last = date.fromisoformat(last_date) if last_date else None
    except Exception:
        last = None

    # أول ستريك
    if last is None or current <= 0:
        new_streak = 1
        new_photos = len(images)
        start_date = today_string
        started = True

    # صور بنفس اليوم
    elif last == today_date:
        new_streak = current
        start_date = (
            member_data.get("streak_start_date")
            or last_date
        )

    # يوم بعد يوم
    elif last == today_date - timedelta(days=1):
        new_streak = current + 1
        start_date = (
            member_data.get("streak_start_date")
            or last_date
        )

    # استخدام درع لتعويض يوم واحد
    elif (
        last == today_date - timedelta(days=2)
        and get_config("shield_enabled")
        and shields > 0
    ):
        new_streak = current + 2
        shields -= 1
        shield_used = True

        start_date = (
            member_data.get("streak_start_date")
            or last_date
        )

    # انقطاع الستريك
    else:
        if current > 0:
            with db() as c:
                c.execute(
                    "INSERT INTO streak_history"
                    "(user_id,start_date,end_date,days,photos,recorded_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        uid,
                        member_data.get("streak_start_date") or last_date,
                        last_date,
                        current,
                        streak_photos,
                        now_iso()
                    )
                )

        new_streak = 1
        new_photos = len(images)
        start_date = today_string
        started = True

        if get_config("remove_rank_roles_on_break"):
            await remove_rank_roles(message.author)

    longest = max(
        int(member_data.get("longest_streak") or 0),
        new_streak
    )

    update_member(
        uid,
        current_streak=new_streak,
        longest_streak=longest,
        total_photos=int(member_data.get("total_photos") or 0) + len(images),
        streak_photos=new_photos,
        last_photo_date=today_string,
        streak_start_date=start_date,
        shields=shields
    )

    # بداية ستريك
    if started:
        if get_config("dm_start_enabled"):
            await dm_user(
                message.guild,
                message.author,
                "streak-start",
                get_config("dm_start_text")
            )

    # تسجيل الدرع المستخدم
    if shield_used:
        with db() as c:
            c.execute(
                "INSERT INTO shield_events(user_id,date_used,note) "
                "VALUES(?,?,?)",
                (
                    uid,
                    today_string,
                    "Automatic shield used"
                )
            )

    # الرياكشن
    if get_config("reaction_enabled"):
        try:
            await message.add_reaction(
                member_emoji(uid)
            )
        except Exception:
            pass

    # رسالة خاص لكل صورة
    if get_config("dm_photo_enabled"):
        template = (
            get_config("dm_photo_template")
            or DEFAULT_CONFIG["dm_photo_template"]
        )

        try:
            dm_text = template.format(
                streak=new_streak,
                photos=new_photos,
                emoji=member_emoji(uid)
            )
        except Exception:
            dm_text = (
                f"🔥 تم تسجيل صورتك!\n"
                f"ستريكك الحالي: {new_streak}"
            )

        await dm_user(
            message.guild,
            message.author,
            "photo-count",
            dm_text
        )

    # فحص الرتب
    await check_ranks(
        message.guild,
        message.author,
        new_streak
    )

    logger.info(
        "Photo registered: user=%s streak=%s photos=%s",
        uid,
        new_streak,
        new_photos
    )
    # =========================
# RANK ROLE MANAGEMENT
# =========================

async def remove_rank_roles(member):
    ranks = get_config("ranks") or []
    roles = []

    for rank in ranks:
        role_id = int(rank.get("role_id") or 0)
        role = member.guild.get_role(role_id)

        if role and role in member.roles:
            roles.append(role)

    if roles:
        try:
            await member.remove_roles(
                *roles,
                reason="ST streak broken"
            )
        except Exception:
            logger.exception("Failed removing rank roles")


# =========================
# DIVIDER
# =========================

async def send_divider(guild):
    if not get_config("divider_enabled"):
        return

    url = (get_config("divider_image_url") or "").strip()
    if not url:
        return

    channel = get_channel(
        guild,
        get_config("streak_channel_id")
    )

    if not channel:
        return

    try:
        import aiohttp
        import io

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=20) as response:
                if response.status != 200:
                    return

                data = await response.read()

                if len(data) > 8 * 1024 * 1024:
                    return

        await channel.send(
            file=discord.File(
                io.BytesIO(data),
                filename="streak-divider.png"
            )
        )

    except Exception:
        logger.exception("Divider failed")


# =========================
# IMAGE DETECTION
# =========================

def is_image_attachment(attachment):
    content_type = (attachment.content_type or "").lower()
    filename = attachment.filename.lower()

    return (
        content_type.startswith("image/")
        or filename.endswith((
            ".png", ".jpg", ".jpeg",
            ".gif", ".webp", ".bmp"
        ))
    )


# =========================
# PHOTO CHANNEL
# =========================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not message.guild:
        return await bot.process_commands(message)

    streak_channel = int(
        get_config("streak_channel_id") or 0
    )

    if (
        streak_channel
        and message.channel.id == streak_channel
        and isinstance(message.author, discord.Member)
    ):
        images = [
            attachment
            for attachment in message.attachments
            if is_image_attachment(attachment)
        ]

        if images:
            try:
                await record_photo(message, images)
            except Exception:
                logger.exception(
                    "Photo processing failed for message %s",
                    message.id
                )

    await bot.process_commands(message)


# =========================
# DELETED PHOTO LOG
# =========================

@bot.event
async def on_message_delete(message):
    if not message.guild:
        return

    if not message.attachments:
        return

    if not get_config("log_deletes_enabled"):
        return

    channel = get_channel(
        message.guild,
        get_config("log_delete_channel_id")
    )

    if not channel:
        return

    deleted_images = [
        attachment
        for attachment in message.attachments
        if is_image_attachment(attachment)
    ]

    if not deleted_images:
        return

    files = []

    for attachment in deleted_images:
        try:
            data = await attachment.read()

            if len(data) <= 8 * 1024 * 1024:
                import io

                files.append(
                    discord.File(
                        io.BytesIO(data),
                        filename=attachment.filename
                    )
                )
        except Exception:
            pass

    embed = make_embed(
        "🗑️ Deleted Streak Photo",
        f"**Member:** {message.author}\n"
        f"**Member ID:** {message.author.id}\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Message ID:** {message.id}\n"
        f"**Images deleted:** {len(deleted_images)}\n"
        f"**Time:** {now_iso()}"
    )

    try:
        await channel.send(
            embed=embed,
            files=files or None
        )
    except Exception:
        logger.exception("Delete photo logging failed")


# =========================
# REACTION LOGGING
# =========================

async def reaction_log(reaction, user, action):
    if user.bot or not reaction.message.guild:
        return

    if not get_config("log_reactions_enabled"):
        return

    guild = reaction.message.guild

    channel = get_channel(
        guild,
        get_config("log_reaction_channel_id")
    )

    if not channel:
        return

    owner = reaction.message.author

    with db() as c:
        c.execute(
            "INSERT INTO reaction_logs"
            "(reactor_id,owner_id,message_id,emoji,action,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                user.id,
                getattr(owner, "id", 0),
                reaction.message.id,
                str(reaction.emoji),
                action,
                now_iso()
            )
        )

    title = (
        "➕ Reaction Added"
        if action == "add"
        else "➖ Reaction Removed"
    )

    try:
        await channel.send(
            embed=make_embed(
                title,
                f"**User:** {user.mention}\n"
                f"**Photo owner:** {owner.mention}\n"
                f"**Emoji:** {reaction.emoji}\n"
                f"**Message:** {reaction.message.jump_url}"
            )
        )
    except Exception:
        logger.exception("Reaction logging failed")


@bot.event
async def on_reaction_add(reaction, user):
    await reaction_log(reaction, user, "add")


@bot.event
async def on_reaction_remove(reaction, user):
    await reaction_log(reaction, user, "remove")
# =========================
# MEMBER COMMANDS
# =========================

@bot.command(name="ستريكي", aliases=["ستريكه"])
async def my_streak(ctx):
    member = get_member(ctx.author.id)

    streak = effective_streak(member)
    longest = int(member["longest_streak"] or 0)
    photos = int(member["total_photos"] or 0)
    shields = int(member["shields"] or 0)

    await ctx.send(embed=make_embed(
        "🔥 ستريكك",
        f"**الستريك الحالي:** {streak} يوم\n"
        f"**أطول ستريك:** {longest} يوم\n"
        f"**إجمالي الصور:** {photos}\n"
        f"**الدروع:** {shields}"
    ))


@bot.command(name="top")
async def top_streak(ctx):
    with db() as c:
        rows = c.execute(
            "SELECT user_id,current_streak,longest_streak "
            "FROM members ORDER BY longest_streak DESC LIMIT 10"
        ).fetchall()

    if not rows:
        return await ctx.send("ما فيه بيانات ستريكات للحين.")

    lines = []

    for i, row in enumerate(rows, 1):
        user = ctx.guild.get_member(row["user_id"])
        name = user.display_name if user else str(row["user_id"])

        lines.append(
            f"**{i}.** {name} — "
            f"🏆 {row['longest_streak']} يوم"
        )

    await ctx.send(embed=make_embed(
        "🏆 Top Streaks",
        "\n".join(lines)
    ))


@bot.command(name="topm")
async def top_photos(ctx):
    with db() as c:
        rows = c.execute(
            "SELECT user_id,total_photos FROM members "
            "ORDER BY total_photos DESC LIMIT 10"
        ).fetchall()

    lines = []

    for i, row in enumerate(rows, 1):
        user = ctx.guild.get_member(row["user_id"])
        name = user.display_name if user else str(row["user_id"])

        lines.append(
            f"**{i}.** {name} — 📸 {row['total_photos']}"
        )

    await ctx.send(embed=make_embed(
        "📸 Top Photos",
        "\n".join(lines) or "لا توجد بيانات."
    ))


@bot.command(name="shield")
async def shield_info(ctx):
    member = get_member(ctx.author.id)

    await ctx.send(embed=make_embed(
        "🛡️ Shields",
        f"دروعك الحالية: **{member['shields']}**\n\n"
        "الدرع يحمي من انقطاع يوم واحد "
        "بحسب نظام الستريك."
    ))


@bot.command(name="سجل_ستريكات")
async def streak_history(ctx):
    with db() as c:
        rows = c.execute(
            "SELECT start_date,end_date,days,photos "
            "FROM streak_history WHERE user_id=? "
            "ORDER BY id DESC LIMIT 10",
            (ctx.author.id,)
        ).fetchall()

    if not rows:
        return await ctx.send("ما عندك ستريكات سابقة مسجلة.")

    lines = []

    for row in rows:
        lines.append(
            f"🔥 **{row['days']} يوم** | "
            f"{row['start_date']} → {row['end_date']} | "
            f"📸 {row['photos']}"
        )

    await ctx.send(embed=make_embed(
        "📜 سجل ستريكاتك",
        "\n".join(lines)
    ))


@bot.command(name="اوامر")
async def help_command(ctx):
    text = (
        "**أوامر ST**\n\n"
        "`-ستريكي` — عرض ستريكك\n"
        "`-ستريكه` — عرض ستريكك\n"
        "`-top` — أطول ستريكات\n"
        "`-topm` — أكثر صور\n"
        "`-shield` — عرض دروعك\n"
        "`-سجل_ستريكات` — سجل ستريكاتك السابقة\n"
        "`-st` — لوحة الإدارة (للإدارة فقط)\n"
    )

    await ctx.send(embed=make_embed(
        "📖 ST Commands",
        text
    ))
# =========================
# ADMIN COMMANDS
# =========================

@bot.command(name="st")
async def admin_panel(ctx):
    if not is_admin(ctx.author):
        return await ctx.send("❌ هذا الأمر للإدارة فقط.")

    text = (
        "**ST ADMIN PANEL**\n\n"
        "`-st channel` — تحديد قناة الستريك\n"
        "`-st photo-log` — قناة تسجيل الصور\n"
        "`-st delete-log` — قناة الصور المحذوفة\n"
        "`-st reaction-log` — قناة الرياكشنات\n"
        "`-st dm-log` — قناة سجل الخاص\n"
        "`-st admin-log` — قناة سجل الإدارة\n"
        "`-st shields @member عدد` — تعديل دروع عضو\n"
        "`-st emoji ايموجي` — تغيير الإيموجي الافتراضي\n"
        "`-st divider رابط` — تغيير صورة الفاصل\n"
        "`-st reset` — إعادة الإعدادات الافتراضية\n"
    )

    await ctx.send(embed=make_embed(
        "🛠️ ST Control Center",
        text
    ))


@bot.command(name="setchannel")
async def set_streak_channel(ctx, channel: discord.TextChannel):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    set_config("streak_channel_id", channel.id)

    await log_admin(
        ctx.guild,
        ctx.author,
        f"Changed streak channel to {channel.id}"
    )

    await ctx.send(f"✅ قناة الستريك: {channel.mention}")


@bot.command(name="setlog")
async def set_log_channel(ctx, log_type, channel: discord.TextChannel):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    log_channels = {
        "photo": "log_photo_channel_id",
        "delete": "log_delete_channel_id",
        "reaction": "log_reaction_channel_id",
        "dm": "log_dm_channel_id",
        "shield": "log_shield_channel_id",
        "admin": "log_admin_channel_id",
    }

    key = log_channels.get(log_type.lower())

    if not key:
        return await ctx.send(
            "الأنواع: photo, delete, reaction, dm, shield, admin"
        )

    set_config(key, channel.id)

    await log_admin(
        ctx.guild,
        ctx.author,
        f"Changed {log_type} log channel to {channel.id}"
    )

    await ctx.send(f"✅ تم تحديد قناة السجل: {channel.mention}")


@bot.command(name="setshields")
async def set_shields(ctx, member: discord.Member, count: int):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    if count < 0 or count > 100:
        return await ctx.send("العدد لازم يكون بين 0 و100.")

    update_member(member.id, shields=count)

    await log_admin(
        ctx.guild,
        ctx.author,
        f"Set shields for {member.id} to {count}"
    )

    await ctx.send(
        f"🛡️ تم تحديد دروع {member.mention} إلى **{count}**."

    )


@bot.command(name="setemoji")
async def set_default_emoji(ctx, *, emoji):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    set_config("default_emoji", emoji)

    await log_admin(
        ctx.guild,
        ctx.author,
        f"Changed default emoji to {emoji}"
    )

    await ctx.send(f"✅ الإيموجي الافتراضي الجديد: {emoji}")


@bot.command(name="setdivider")
async def set_divider(ctx, *, url):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    if not url.startswith("https://"):
        return await ctx.send("❌ أرسل رابط HTTPS.")

    set_config("divider_image_url", url)
    set_config("divider_enabled", True)

    await log_admin(
        ctx.guild,
        ctx.author,
        "Changed streak divider"
    )

    await ctx.send("✅ تم تحديث صورة الفاصل.")


@bot.command(name="resetst")
async def reset_settings(ctx):
    if not is_admin(ctx.author):
        return await ctx.send("❌ للإدارة فقط.")

    for key, value in DEFAULT_CONFIG.items():
        set_config(key, value)

    await log_admin(
        ctx.guild,
        ctx.author,
        "Reset configuration"
    )

    await ctx.send(
        "⚠️ تمت إعادة الإعدادات الافتراضية. "
        "بيانات الستريكات لم تُحذف."
    )


# =========================
# READY
# =========================

@bot.event
async def on_ready():
    logger.info("ST connected as %s", bot.user)

    for guild in bot.guilds:
        logger.info("Connected to: %s", guild.name)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("❌ ناقص بيانات للأمر.")

    if isinstance(error, commands.BadArgument):
        return await ctx.send("❌ تأكد من العضو أو القناة.")

    logger.exception("Command error", exc_info=error)

    try:
        await ctx.send("⚠️ صار خطأ أثناء تنفيذ الأمر.")
    except Exception:
        pass


# =========================
# RUN BOT
# =========================

bot.run(TOKEN)
