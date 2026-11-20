# -*- coding: utf-8 -*-
"""ST streak bot - single-file Discord bot.
Requires: discord.py 2.x, aiohttp (discord.py dependency)
Railway variables: DISCORD_TOKEN (required), DB_PATH (optional; recommended /data/streaks.sqlite)
"""
import os, json, sqlite3, asyncio, logging, io, re
from contextlib import contextmanager
import aiohttp
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional
import discord
from discord.ext import commands


# ------------------------------ Runtime ------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ST")
TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "streaks.sqlite3")
if DB_PATH != ":memory:" and os.path.dirname(DB_PATH):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
TZ = ZoneInfo("Asia/Kuwait")
PREFIX = "-"
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable (set it in Railway Variables).")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None, case_insensitive=True)

# One active interactive configuration session per administrator.
ADMIN_INPUT_LOCKS = {}
PHOTO_LOCKS = {}

# ------------------------------ Database ------------------------------
DEFAULT_CONFIG = {
    "streak_channel_id": 0,
    "divider_image_url": "",
    "divider_enabled": True,
    "dm_photo_enabled": True,
    "dm_photo_template": "تم تسجيل صورتك! ستريكك الحالي = {streak} يوم {emoji}\nعدد صورك: {photos}",
    "dm_start_enabled": True,
    "dm_start_text": "بديت ستريك يا وحش استمر",
    "dm_rank_enabled": True,
    "dm_rank_template": "🏆 مبروك حصولك على رتبة {rank}! وصلت {days} يوم.",
    "reaction_enabled": True,
    "default_emoji": "🔥",
    "shield_enabled": True,
    "default_shields": 1,
    "keep_old_rank_roles": True,
    "remove_rank_roles_on_break": False,
    "customization_enabled": True,
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
    "ranks": [
        {"days": 1, "name": "Streak Up — Day 1", "role_id": 0},
        {"days": 7, "name": "Streak Rookie", "role_id": 0},
        {"days": 14, "name": "Streak Keeper", "role_id": 0},
        {"days": 30, "name": "Streak Master", "role_id": 0},
        {"days": 100, "name": "Unstoppable", "role_id": 0},
        {"days": 365, "name": "Streak Legend", "role_id": 0},
    ],
}

@contextmanager
def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members (
          user_id INTEGER PRIMARY KEY, current_streak INTEGER NOT NULL DEFAULT 0,
          longest_streak INTEGER NOT NULL DEFAULT 0, total_photos INTEGER NOT NULL DEFAULT 0,
          streak_photos INTEGER NOT NULL DEFAULT 0, last_photo_date TEXT,
          streak_start_date TEXT, shields INTEGER NOT NULL DEFAULT 0,
          personal_emoji TEXT, personal_title TEXT, show_title INTEGER NOT NULL DEFAULT 1,
          style TEXT NOT NULL DEFAULT 'classic'
        );
        CREATE TABLE IF NOT EXISTS processed_messages (message_id INTEGER PRIMARY KEY, processed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS streak_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
          start_date TEXT, end_date TEXT, days INTEGER NOT NULL, photos INTEGER NOT NULL,
          recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS milestone_awards (user_id INTEGER NOT NULL, days INTEGER NOT NULL, awarded_at TEXT NOT NULL, PRIMARY KEY(user_id,days));
        CREATE TABLE IF NOT EXISTS shield_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, date_used TEXT, note TEXT);
        CREATE TABLE IF NOT EXISTS dm_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, content TEXT, sent INTEGER, created_at TEXT);
        CREATE TABLE IF NOT EXISTS admin_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS reaction_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, reactor_id INTEGER, owner_id INTEGER, message_id INTEGER, emoji TEXT, action TEXT, created_at TEXT);
        """)
        for k, v in DEFAULT_CONFIG.items():
            c.execute("INSERT OR IGNORE INTO config(key,value) VALUES(?,?)", (k, json.dumps(v, ensure_ascii=False)))
        # Compatibility with older DBs: add missing columns if necessary.
        cols = {r[1] for r in c.execute("PRAGMA table_info(members)").fetchall()}
        for name, decl in [("streak_start_date", "TEXT"), ("personal_emoji", "TEXT"), ("personal_title", "TEXT"), ("show_title", "INTEGER NOT NULL DEFAULT 1"), ("style", "TEXT NOT NULL DEFAULT 'classic'")]:
            if name not in cols:
                c.execute(f"ALTER TABLE members ADD COLUMN {name} {decl}")

init_db()

def get_config(key):
    with db_connect() as c:
        row = c.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    if row is None:
        return DEFAULT_CONFIG.get(key)
    try:
        return json.loads(row[0])
    except Exception:
        return DEFAULT_CONFIG.get(key)

def set_config(key, value):
    with db_connect() as c:
        c.execute("INSERT INTO config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

def get_member(uid):
    with db_connect() as c:
        c.execute("INSERT OR IGNORE INTO members(user_id,shields) VALUES(?,?)", (uid, int(get_config("default_shields") or 0)))
        row = c.execute("SELECT * FROM members WHERE user_id=?", (uid,)).fetchone()
    return dict(row)

def update_member(uid, **fields):
    if not fields:
        return
    allowed = {"current_streak", "longest_streak", "total_photos", "streak_photos", "last_photo_date", "streak_start_date", "shields", "personal_emoji", "personal_title", "show_title", "style"}
    fields = {k:v for k,v in fields.items() if k in allowed}
    if not fields:
        return
    with db_connect() as c:
        c.execute("INSERT OR IGNORE INTO members(user_id,shields) VALUES(?,?)", (uid, int(get_config("default_shields") or 0)))
        assignments = ",".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE members SET {assignments} WHERE user_id=?", (*fields.values(), uid))

def today():
    return datetime.now(TZ).date()

def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")

def fmt_date(s):
    if not s:
        return "—"
    try:
        return date.fromisoformat(s).strftime("%Y-%m-%d")
    except Exception:
        return str(s)

def is_admin(member: discord.Member):
    return member.guild_permissions.administrator or member.guild_permissions.manage_guild

def safe_emoji(value):
    return str(value or get_config("default_emoji") or "🔥")

def member_emoji(uid):
    m = get_member(uid)
    return safe_emoji(m.get("personal_emoji"))

def effective_streak(member_data):
    """Hide stale streaks; a one-day miss may be protected on the next upload."""
    streak = int(member_data.get("current_streak") or 0)
    last = member_data.get("last_photo_date")
    if streak <= 0 or not last:
        return 0
    try:
        gap = (today() - date.fromisoformat(last)).days
    except Exception:
        return 0
    if gap in (0, 1):
        return streak
    if gap == 2 and get_config("shield_enabled") and int(member_data.get("shields") or 0) > 0:
        return streak
    return 0

# ------------------------------ Embed / helpers ------------------------------
COLOR = 0x5865F2
DARK = 0x2B2D31

def embed(title, description="", color=COLOR):
    e = discord.Embed(title=title, description=description, color=color, timestamp=datetime.now(TZ))
    e.set_footer(text="ST • Streak System")
    return e


def channel_by_id(guild, channel_id):
    if not channel_id:
        return None
    ch = guild.get_channel(int(channel_id))
    return ch

async def send_log(guild, key, enabled_key, message=None, *, embed_obj=None, files=None):
    if not get_config(enabled_key):
        return
    ch = channel_by_id(guild, get_config(key))
    if not ch:
        return
    try:
        await ch.send(content=message, embed=embed_obj, files=files or [])
    except Exception as ex:
        log.warning("log send failed (%s): %s", key, ex)
        async def log_admin(guild, admin, action):
    with db_connect() as c:
        c.execute("INSERT INTO admin_logs(admin_id,action,created_at) VALUES(?,?,?)", (admin.id, action, now_iso()))
    if get_config("log_admin_enabled"):
        ch = channel_by_id(guild, get_config("log_admin_channel_id"))
        if ch:
            try:
                await ch.send(embed=embed("🛡️ Admin Activity", f"**Admin:** {admin.mention} (`{admin.id}`)\n**Action:** {discord.utils.escape_markdown(action)}"))
            except Exception:
                pass

async def log_dm(guild, user, kind, content, sent):
    with db_connect() as c:
        c.execute("INSERT INTO dm_logs(user_id,kind,content,sent,created_at) VALUES(?,?,?,?,?)", (user.id, kind, content, int(sent), now_iso()))
    if get_config("log_dms_enabled"):
        ch = channel_by_id(guild, get_config("log_dm_channel_id"))
        if ch:
            status = "وصلت" if sent else "فشل الإرسال"
            try:
                await ch.send(embed=embed("📨 DM Log", f"**العضو:** {user.mention} (`{user.id}`)\n**النوع:** {kind}\n**الحالة:** {status}\n**المحتوى:**\n{discord.utils.escape_markdown(content)[:3500]}"))
            except Exception:
                pass

async def dm_user(guild, user, kind, content):
    success = False
    try:
        await user.send(content)
        success = True
    except (discord.Forbidden, discord.HTTPException):
        success = False
    await log_dm(guild, user, kind, content, success)
    return success


async def save_image_for_log(attachment):
    try:
        data = await attachment.read()
        if len(data) > 8 * 1024 * 1024:
            return None
        return discord.File(io.BytesIO(data), filename=attachment.filename or "image.png")
    except Exception as e:
        log.warning("Could not cache attachment: %s", e)
        return None


async def send_divider(guild, user):
    url = (get_config("divider_image_url") or "").strip()
    if not get_config("divider_enabled") or not url:
        return
    # Divider is sent as a plain image attachment, without text or embed.
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise RuntimeError(f"image URL HTTP {response.status}")
                content_type = response.headers.get("Content-Type", "")
                if not content_type.lower().startswith("image/"):
                    raise RuntimeError("URL did not return an image")
                data = await response.read()
                if len(data) > 8 * 1024 * 1024:
                    raise RuntimeError("divider image exceeds 8 MB")
        ch = channel_by_id(guild, get_config("streak_channel_id"))
        if ch:
            await ch.send(file=discord.File(io.BytesIO(data), filename="streak-divider.png"))
    except Exception as ex:
        log.warning("Divider failed: %s", ex)

# ------------------------------ Streak core ------------------------------
async def record_photo(message: discord.Message, image_attachments):
    # Serialize uploads by member to prevent simultaneous uploads overwriting counters.
    lock = PHOTO_LOCKS.setdefault(message.author.id, asyncio.Lock())
    async with lock:
        await _record_photo_locked(message, image_attachments)

async def _record_photo_locked(message: discord.Message, image_attachments):
    uid = message.author.id
    today_str = today().isoformat()
    with db_connect() as c:
        inserted = c.execute("INSERT OR IGNORE INTO processed_messages(message_id,processed_at) VALUES(?,?)", (message.id, now_iso())).rowcount
        if inserted == 0:
            return
    m = get_member(uid)
    previous = m.get("last_photo_date")
    current = int(m.get("current_streak") or 0)
    streak_photos = int(m.get("streak_photos") or 0)
    shields = int(m.get("shields") or 0)
    new_streak = current
    new_photos = streak_photos + len(image_attachments)
    started = False
    shield_used = False
    last = None
    try:
        last = date.fromisoformat(previous) if previous else None
    except Exception:
        last = None
    td = today()

    if last is None or current <= 0:
        new_streak = 1
        new_photos = len(image_attachments)
        started = True
        start_date = today_str

    elif last == td:
        # Same local calendar date: photos increment; streak doesn't.
        new_streak = current
        start_date = m.get("streak_start_date") or previous

    elif last == td - timedelta(days=1):
        new_streak = current + 1
        start_date = m.get("streak_start_date") or previous

    elif last == td - timedelta(days=2) and get_config("shield_enabled") and shields > 0:
        # Shield covers exactly one missed calendar day.
        new_streak = current + 2  # count the protected day and today's photo
        start_date = m.get("streak_start_date") or previous
        shields -= 1
        shield_used = True

    else:
        # Close the old streak record before starting over.
        if current > 0:
            with db_connect() as c:
                c.execute(
                    "INSERT INTO streak_history(user_id,start_date,end_date,days,photos,recorded_at) VALUES(?,?,?,?,?,?)",
                    (uid, m.get("streak_start_date") or previous, previous, current, streak_photos, now_iso())
                )
        new_streak = 1
        new_photos = len(image_attachments)
        started = True
        start_date = today_str
        if get_config("remove_rank_roles_on_break"):
            await remove_rank_roles(message.author)

    longest = max(int(m.get("longest_streak") or 0), new_streak)
    update_member(
        uid,
        current_streak=new_streak,
        longest_streak=longest,
        total_photos=int(m.get("total_photos") or 0) + len(image_attachments),
        streak_photos=new_photos,
        last_photo_date=today_str,
        streak_start_date=start_date,
        shields=shields
    )

    if started:
        await send_divider(message.guild, message.author)
        if get_config("dm_start_enabled"):
            await dm_user(
                message.guild,
                message.author,
                "streak-start",
                get_config("dm_start_text") or DEFAULT_CONFIG["dm_start_text"]
            )

    if shield_used:
        with db_connect() as c:
            c.execute(
                "INSERT INTO shield_events(user_id,date_used,note) VALUES(?,?,?)",
                (uid, today_str, "Automatic shield consumed for one missed day")
            )
        if get_config("log_shields_enabled"):
            await send_log(
                message.guild,
                "log_shield_channel_id",
                "log_shields_enabled",
                embed_obj=embed("🛡️ Shield used", f"{message.author.mention} used one shield. Remaining: **{shields}**")
            )

    # React only once per image-bearing message.
    if get_config("reaction_enabled"):
        try:
            await message.add_reaction(safe_emoji(m.get("personal_emoji")))
        except Exception:
            pass

    if get_config("dm_photo_enabled"):
        template = get_config("dm_photo_template") or DEFAULT_CONFIG["dm_photo_template"]
        try:
            text = template.format(
                streak=new_streak,
                emoji=safe_emoji(m.get("personal_emoji")),
                photos=new_photos
            )
        except (KeyError, ValueError, IndexError):
            text = DEFAULT_CONFIG["dm_photo_template"].format(
                streak=new_streak,
                emoji=safe_emoji(m.get("personal_emoji")),
                photos=new_photos
            )
        await dm_user(message.guild, message.author, "photo-count", text)

    await check_ranks(message.guild, message.author, new_streak)

    # Log each uploaded image as its own entry.
    if get_config("log_photos_enabled"):
        ch = channel_by_id(message.guild, get_config("log_photo_channel_id"))
        if ch:
            for att in image_attachments:
                f = await save_image_for_log(att)
                e = embed(
                    "📸 Streak Photo",
                    f"**Member:** {message.author.mention} (`{uid}`)\n"
                    f"**Streak:** {new_streak} days\n"
                    f"**Photos in streak:** {new_photos}\n"
                    f"**Uploaded:** {message.jump_url}"
                )
                try:
                    if f:
                        await ch.send(embed=e, file=f)
                    else:
                        await ch.send(embed=e)
                except Exception as ex:
                    log.warning("Photo log failed: %s", ex)
                    async def check_ranks(guild, member, days):
    ranks = sorted(get_config("ranks") or [], key=lambda x: int(x.get("days", 0)))
    for rank in ranks:
        milestone = int(rank.get("days", 0))
        if milestone <= 0 or days < milestone:
            continue

        # Recheck rank roles so removed roles can be reassigned later.
        role_id = int(rank.get("role_id") or 0)
        role = guild.get_role(role_id) if role_id else None
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"ST streak milestone {milestone}")
            except discord.Forbidden:
                log.warning("No role permission for milestone role %s", role_id)
            except discord.HTTPException:
                log.exception("Failed assigning milestone role %s", role_id)

        with db_connect() as c:
            exists = c.execute(
                "SELECT 1 FROM milestone_awards WHERE user_id=? AND days=?",
                (member.id, milestone)
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT OR IGNORE INTO milestone_awards(user_id,days,awarded_at) VALUES(?,?,?)",
                    (member.id, milestone, now_iso())
                )

        if exists:
            continue

        if get_config("dm_rank_enabled"):
            template = get_config("dm_rank_template") or DEFAULT_CONFIG["dm_rank_template"]
            try:
                msg = template.format(rank=rank.get("name", "Rank"), days=milestone)
            except (KeyError, ValueError, IndexError):
                msg = DEFAULT_CONFIG["dm_rank_template"].format(
                    rank=rank.get("name", "Rank"), days=milestone
                )
            await dm_user(guild, member, "rank-milestone", msg)

        if not get_config("keep_old_rank_roles"):
            old_roles = [
                guild.get_role(int(r.get("role_id") or 0))
                for r in ranks
                if int(r.get("days", 0)) < milestone
            ]
            old_roles = [r for r in old_roles if r and r in member.roles]
            if old_roles:
                try:
                    await member.remove_roles(*old_roles, reason="ST rank upgrade")
                except discord.HTTPException:
                    log.exception("Failed removing previous streak roles")


async def remove_rank_roles(member):
    guild = member.guild
    roles = [
        guild.get_role(int(r.get("role_id") or 0))
        for r in (get_config("ranks") or [])
    ]
    roles = [r for r in roles if r and r in member.roles]
    if roles:
        try:
            await member.remove_roles(*roles, reason="Streak reset")
        except Exception:
            pass


# ------------------------------ Commands ------------------------------
@bot.command(name="ستريكي")
async def my_streak(ctx):
    m = get_member(ctx.author.id)
    e = embed(f"📸 ستريك {ctx.author.display_name}")
    e.set_thumbnail(url=ctx.author.display_avatar.url)
    emoji = safe_emoji(m.get("personal_emoji"))

    e.add_field(name="الستريك الحالي", value=f"{emoji} **{effective_streak(m)} يوم**", inline=True)
    e.add_field(name="أطول ستريك", value=f"🏆 **{m['longest_streak']} يوم**", inline=True)
    e.add_field(name="صور الستريك الحالي", value=f"📷 **{m['streak_photos']}**", inline=True)
    e.add_field(name="إجمالي الصور", value=f"🖼️ **{m['total_photos']}**", inline=True)
    e.add_field(name="الدروع", value=f"🛡️ **{m['shields']}**", inline=True)

    if m.get("personal_title") and m.get("show_title"):
        e.add_field(name="اللقب", value=m["personal_title"], inline=False)
    await ctx.send(embed=e)


@bot.command(name="ستريكه")
async def user_streak(ctx, member: discord.Member):
    m = get_member(member.id)
    e = embed(f"📸 ستريك {member.display_name}")
    e.set_thumbnail(url=member.display_avatar.url)

    e.add_field(
        name="الستريك الحالي",
        value=f"{safe_emoji(m.get('personal_emoji'))} **{effective_streak(m)} يوم**",
        inline=True
    )
    e.add_field(name="أطول ستريك", value=f"🏆 **{m['longest_streak']} يوم**", inline=True)
    e.add_field(name="صور الستريك الحالي", value=f"📷 **{m['streak_photos']}**", inline=True)
    e.add_field(name="إجمالي الصور", value=f"🖼️ **{m['total_photos']}**", inline=True)

    if m.get("personal_title") and m.get("show_title"):
        e.add_field(name="اللقب", value=m["personal_title"], inline=False)
    await ctx.send(embed=e)


@bot.command(name="tops")
async def top_current(ctx):
    await send_leaderboard(ctx, "current_streak", "أعلى الستريكات الحالية", "يوم", "🔥")


@bot.command(name="topc")
async def top_photos(ctx):
    await send_leaderboard(ctx, "total_photos", "ترتيب إجمالي الصور", "صورة", "📸")


@bot.command(name="topm")
async def top_longest(ctx):
    await send_leaderboard(ctx, "longest_streak", "أطول ستريكات تاريخية", "يوم", "🏆")


@bot.command(name="top")
async def top_all(ctx):
    e = embed("🏅 لوحة الترتيب الشاملة")
    rows = await query_top("current_streak", 5)
    rows2 = await query_top("total_photos", 5)
    rows3 = await query_top("longest_streak", 5)

    e.add_field(name="🔥 الستريك الحالي", value=format_rows(ctx.guild, rows, "current_streak", "يوم"), inline=False)
    e.add_field(name="📸 الصور", value=format_rows(ctx.guild, rows2, "total_photos", "صورة"), inline=False)
    e.add_field(name="🏆 أطول ستريك", value=format_rows(ctx.guild, rows3, "longest_streak", "يوم"), inline=False)
    await ctx.send(embed=e)


async def query_top(column, limit=10):
    allowed = {"current_streak", "total_photos", "longest_streak"}
    if column not in allowed:
        return []

    if column == "current_streak":
        with db_connect() as c:
            rows = c.execute("SELECT * FROM members WHERE current_streak>0").fetchall()

        scored = []
        for row in rows:
            m = dict(row)
            score = effective_streak(m)
            if score > 0:
                scored.append({"user_id": m["user_id"], "score": score})

        return sorted(
            scored,
            key=lambda x: (-int(x["score"]), int(x["user_id"]))
        )[:limit]

    with db_connect() as c:
        return c.execute(
            f"SELECT user_id,{column} AS score FROM members "
            f"WHERE {column}>0 ORDER BY {column} DESC,user_id ASC LIMIT ?",
            (limit,)
        ).fetchall()


def format_rows(guild, rows, column, unit):
    if not rows:
        return "لا توجد بيانات حتى الآن."

    lines = []
    for i, r in enumerate(rows, 1):
        member = guild.get_member(int(r["user_id"]))
        name = member.display_name if member else f"عضو {r['user_id']}"
        lines.append(
            f"**{i}.** {discord.utils.escape_markdown(name)} — **{r['score']}** {unit}"
        )
    return "\n".join(lines)


async def send_leaderboard(ctx, column, title, unit, emoji):
    rows = await query_top(column, 10)
    e = embed(f"{emoji} {title}", format_rows(ctx.guild, rows, column, unit))
    await ctx.send(embed=e)


@bot.command(name="ايموجي")
async def emoji_pick(ctx):
    if not get_config("customization_enabled"):
        return await ctx.send("التخصيصات الشخصية متوقفة حاليًا.")

    choices = [
        ("🔥", "🔥", None), ("⚡", "⚡", None), ("💫", "💫", None),
        ("✨", "✨", None), ("🌟", "🌟", None), ("💎", "💎", None),
        ("🖤", "🖤", None), ("💜", "💜", None), ("💙", "💙", None),
        ("💚", "💚", None), ("❤️", "❤️", None), ("🩵", "🩵", None),
        ("🌙", "🌙", None), ("☀️", "☀️", None), ("🍀", "🍀", None),
        ("🎯", "🎯", None), ("👑", "👑", None), ("🐉", "🐉", None),
        ("🦋", "🦋", None), ("🌈", "🌈", None)
    ]

    # Include up to five server emojis, keeping the Select under Discord's 25-option limit.
    for custom in ctx.guild.emojis[:5]:
        choices.append((custom.name[:100], str(custom), custom))

    view = EmojiView(ctx.author.id, choices)
    await ctx.send(
        embed=embed("اختيار إيموجي الستريك", "اختر الإيموجي من القائمة."),
        view=view
    )


class EmojiView(discord.ui.View):
    def __init__(self, owner_id, choices):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.add_item(EmojiSelect(choices))

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("هذه القائمة ليست لك.", ephemeral=True)
            return False
        return True


class EmojiSelect(discord.ui.Select):
    def __init__(self, choices):
        options = [
            discord.SelectOption(label=label, value=value, emoji=emoji)
            if emoji else discord.SelectOption(label=label, value=value)
            for label, value, emoji in choices
        ]
        super().__init__(placeholder="اختر إيموجي…", options=options)

    async def callback(self, interaction):
        update_member(interaction.user.id, personal_emoji=self.values[0])
        await interaction.response.edit_message(
            embed=embed("تم تغيير الإيموجي", f"إيموجيك الجديد: {self.values[0]}"),
            view=None
        )


async def streak_history_command(ctx):
    with db_connect() as c:
        rows = c.execute(
            "SELECT user_id,start_date,end_date,days,photos "
            "FROM streak_history ORDER BY days DESC, photos DESC LIMIT 15"
        ).fetchall()

    if not rows:
        return await ctx.send(
            embed=embed("📚 سجل الستريكات", "ما فيه ستريكات سابقة محفوظة حتى الآن.")
        )

    lines = []
    for r in rows:
        member = ctx.guild.get_member(int(r["user_id"]))
        nm = member.display_name if member else str(r["user_id"])
        lines.append(
            f"**{discord.utils.escape_markdown(nm)}** — {r['days']} يوم | "
            f"{r['photos']} صورة | {fmt_date(r['start_date'])} → {fmt_date(r['end_date'])}"
        )

    await ctx.send(embed=embed("📚 سجل الستريكات التاريخي", "\n".join(lines)))


@bot.command(name="اوامر")
async def commands_list(ctx):
    e = embed("📜 ST | قائمة الأوامر", "كل الأوامر المتاحة للأعضاء — استخدم البادئة `-`")
    e.add_field(
        name="📈 إحصائيات الستريك",
        value="`-ستريكي`\nعرض ستريكك الحالي، أطول ستريك، الصور، والدروع.\n\n"
              "`-ستريكه @عضو`\nعرض إحصائيات عضو آخر.",
        inline=False
    )
    e.add_field(
        name="🎨 تخصيص",
        value="`-ايموجي`\nاختيار إيموجي خاص يظهر مع ستريكك.",
        inline=False
    )
    e.add_field(
        name="🏅 لوحات الترتيب",
        value="`-tops` — أعلى الستريكات الحالية\n"
              "`-topc` — أكثر الأعضاء رفعًا للصور\n"
              "`-topm` — أطول ستريكات مسجلة\n"
              "`-top` — لوحة ترتيب مختصرة تجمع الكل",
        inline=False
    )
    e.add_field(
        name="📚 السجل",
        value="`-سجل ستريكات` — عرض السجلات التاريخية\n"
              "`-اوامر` — عرض قائمة الأوامر",
        inline=False
    )
    if ctx.guild.icon:
        e.set_thumbnail(url=ctx.guild.icon.url)
    await ctx.send(embed=e)
    # ------------------------------ Admin panel ------------------------------
ADMIN_OPTIONS = [
    ("channels", "📍 القنوات والسجلات", "ضبط قناة الستريك وقنوات السجلات"),
    ("messages", "💬 رسائل الخاص", "تعديل رسائل الستريك والرتب"),
    ("shields", "🛡️ الدروع", "تفعيل الحماية والعدد الافتراضي"),
    ("divider", "🖼️ صورة بداية الستريك", "رابط صورة الفاصل"),
    ("ranks", "🏆 الرتب", "تعديل الرتب وأيامها والرولات"),
    ("features", "⚙️ الأنظمة", "تشغيل وإيقاف الأنظمة"),
    ("member", "👤 تخصيص عضو", "تخصيص عضو محدد"),
    ("reset", "♻️ إعادة ضبط", "إعادة إعدادات النظام الافتراضية"),
]


class AdminPanel(discord.ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.add_item(AdminSelect())
        self.add_item(ClosePanel())

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "لوحة التحكم خاصة بالإداري الذي فتحها.",
                ephemeral=True
            )
            return False

        if not is_admin(interaction.user):
            await interaction.response.send_message(
                "تحتاج صلاحية إدارة السيرفر.",
                ephemeral=True
            )
            return False
        return True


class AdminSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=label,
                description=desc,
                value=key,
                emoji=label.split()[0]
            )
            for key, label, desc in ADMIN_OPTIONS
        ]
        super().__init__(
            placeholder="اختر قسمًا من لوحة التحكم…",
            options=options
        )

    async def callback(self, interaction):
        await interaction.response.defer()
        await admin_section(interaction, self.values[0])


class ClosePanel(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="إغلاق اللوحة",
            style=discord.ButtonStyle.secondary,
            emoji="✖"
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="تم إغلاق لوحة التحكم.",
            embed=None,
            view=None
        )


async def prompt_admin(interaction, prompt, parser=None, timeout=150):
    # Supports both Discord interactions (ephemeral prompts) and command contexts.
    if hasattr(interaction, "followup"):
        await interaction.followup.send(prompt, ephemeral=True)
        author_id = interaction.user.id
        channel_id = interaction.channel.id

        async def reply(text):
            await interaction.followup.send(text, ephemeral=True)

    else:
        await interaction.send(prompt)
        author_id = interaction.author.id
        channel_id = interaction.channel.id

        async def reply(text):
            await interaction.send(text)

    def check(m):
        return m.author.id == author_id and m.channel.id == channel_id

    try:
        msg = await bot.wait_for("message", check=check, timeout=timeout)
    except asyncio.TimeoutError:
        await reply("انتهى وقت إدخال القيمة.")
        return None

    raw = msg.content.strip()
    if raw.lower() in {"الغاء", "إلغاء", "cancel"}:
        return None

    if parser:
        try:
            return parser(raw)
        except Exception:
            await reply("القيمة غير صحيحة. ألغيت العملية.")
            return None

    return raw


async def admin_section(interaction, section):
    guild = interaction.guild
    admin = interaction.user

    lock = ADMIN_INPUT_LOCKS.setdefault(admin.id, asyncio.Lock())
    if lock.locked():
        return await interaction.followup.send(
            "عندك جلسة إعدادات مفتوحة بالفعل. "
            "خلّصها أو اكتب `إلغاء` قبل فتح قسم آخر.",
            ephemeral=True
        )

    await lock.acquire()
    try:
        await _admin_section_locked(interaction, section)
    finally:
        if lock.locked():
            lock.release()


async def _admin_section_locked(interaction, section):
    guild = interaction.guild
    admin = interaction.user
        if section == "channels":
        options = [
            ("streak_channel_id", "قناة الستريك"),
            ("log_photo_channel_id", "سجل الصور"),
            ("log_delete_channel_id", "سجل حذف الصور"),
            ("log_reaction_channel_id", "سجل الرياكشنات"),
            ("log_dm_channel_id", "سجل الخاص"),
            ("log_shield_channel_id", "سجل الدروع"),
            ("log_admin_channel_id", "سجل الإدارة")
        ]

        prompt = (
            "**القنوات الحالية:**\n"
            + "\n".join(
                f"{label}: `{get_config(key) or 'غير محدد'}`"
                for key, label in options
            )
            + "\n\nأرسل اسم الإعداد المطلوب (مثل `قناة الستريك`) "
            "ثم أرسل ID القناة. أو اكتب `إلغاء`."
        )

        await interaction.followup.send(prompt, ephemeral=True)

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            first = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        map_label = {label: key for key, label in options}
        key = map_label.get(first.content.strip())

        if not key:
            return await interaction.followup.send(
                "اسم إعداد غير معروف. ألغيت.",
                ephemeral=True
            )

        val = await prompt_admin(
            interaction,
            "أرسل ID القناة (أو `0` لتعطيلها).",
            lambda s: int(s)
        )

        if val is not None:
            if val != 0:
                channel = guild.get_channel(val)

                if channel is None:
                    try:
                        channel = await guild.fetch_channel(val)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        channel = None

                if channel is None or not hasattr(channel, "send"):
                    return await interaction.followup.send(
                        "ما قدرت ألقى قناة بهذا الـID داخل السيرفر. "
                        "تأكد من الرقم وحاول مرة ثانية.",
                        ephemeral=True
                    )

            set_config(key, val)
            await log_admin(guild, admin, f"Set {key} to {val}")
            await interaction.followup.send("تم حفظ الإعداد.", ephemeral=True)

    elif section == "messages":
        opts = {
            "رسالة الصورة": "dm_photo_template",
            "رسالة بداية": "dm_start_text",
            "رسالة الرتبة": "dm_rank_template"
        }

        await interaction.followup.send(
            "اختر الرسالة التي تريد تعديلها:\n"
            "**رسالة الصورة** / **رسالة بداية** / **رسالة الرتبة**",
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            first = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        key = opts.get(first.content.strip())
        if not key:
            return await interaction.followup.send(
                "الاختيار غير معروف.",
                ephemeral=True
            )

        old = get_config(key)
        await interaction.followup.send(
            f"**النص الحالي:**\n```{old}```\n"
            "أرسل النص الجديد.\n"
            "متغيرات رسالة الصور: `{streak}`, `{emoji}`, `{photos}`.",
            ephemeral=True
        )

        val = await prompt_admin(
            interaction,
            "أرسل النص الجديد (أو `إلغاء`)."
        )

        if val is not None:
            set_config(key, val)
            await log_admin(guild, admin, f"Changed {key}")
            await interaction.followup.send("تم التعديل.", ephemeral=True)

    elif section == "shields":
        await interaction.followup.send(
            f"الحماية: {'مفعلة' if get_config('shield_enabled') else 'متوقفة'}\n"
            f"العدد الافتراضي: {get_config('default_shields')}\n"
            "أرسل `تفعيل` أو `تعطيل` أو `عدد` أو `عضو`.",
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        action = msg.content.strip()

        if action == "تفعيل":
            set_config("shield_enabled", True)
            await log_admin(guild, admin, "Enabled shields")

        elif action == "تعطيل":
            set_config("shield_enabled", False)
            await log_admin(guild, admin, "Disabled shields")

        elif action == "عدد":
            val = await prompt_admin(
                interaction,
                "أرسل العدد الافتراضي للدروع (0-20).",
                lambda x: max(0, min(20, int(x)))
            )
            if val is not None:
                set_config("default_shields", val)
                await log_admin(guild, admin, f"Default shields set {val}")

        elif action == "عضو":
            val = await prompt_admin(
                interaction,
                "أرسل ID العضو.",
                lambda x: int(x)
            )

            if val is not None:
                target = guild.get_member(val)

                if target is None:
                    try:
                        target = await guild.fetch_member(val)
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        target = None

                if target is None:
                    return await interaction.followup.send(
                        "العضو غير موجود في السيرفر.",
                        ephemeral=True
                    )

                count = await prompt_admin(
                    interaction,
                    "أرسل عدد الدروع لهذا العضو (0-100).",
                lambda x: max(0, min(100, int(x)))
                )

                if count is not None:
                    update_member(val, shields=count)
                    await log_admin(guild, admin, f"Set shields for {val} to {count}")

        await interaction.followup.send(
            "تم تنفيذ الطلب إذا كانت البيانات صحيحة.",
            ephemeral=True
        )

    elif section == "divider":
        await interaction.followup.send(
            f"رابط صورة الفاصل الحالي: "
            f"{get_config('divider_image_url') or 'غير محدد'}\n"
            "أرسل `تفعيل` أو `تعطيل` أو `رابط`.",
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        action = msg.content.strip()

        if action == "تفعيل":
            set_config("divider_enabled", True)
            await log_admin(guild, admin, "Enabled divider")

        elif action == "تعطيل":
            set_config("divider_enabled", False)
            await log_admin(guild, admin, "Disabled divider")

        elif action == "رابط":
            val = await prompt_admin(
                interaction,
                "أرسل رابط صورة مباشر (أو إلغاء).",
                lambda s: s if re.match(r"^https://\S+$", s)
                else (_ for _ in ()).throw(ValueError())
            )
            if val:
                set_config("divider_image_url", val)
                await log_admin(guild, admin, "Changed divider image URL")

        await interaction.followup.send("تم.", ephemeral=True)

    elif section == "ranks":
        ranks = get_config("ranks") or []
        display = "\n".join(
            f"{r['days']} يوم — {r['name']} — role ID: {r.get('role_id', 0)}"
            for r in ranks
        )

        await interaction.followup.send(
            f"**الرتب الحالية:**\n{display}\n\n"
            "أرسل `تعديل` لتعديل رتبة، `إضافة` لإضافة رتبة، "
            "`الاحتفاظ` للتبديل بين الاحتفاظ/استبدال الرتب.",
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        if msg.content.strip() == "الاحتفاظ":
            set_config(
                "keep_old_rank_roles",
                not get_config("keep_old_rank_roles")
            )
            await log_admin(guild, admin, "Toggled keep old rank roles")

        elif msg.content.strip() in ("تعديل", "إضافة"):
            if msg.content.strip() == "تعديل":
                day = await prompt_admin(
                    interaction,
                    "أرسل عدد أيام الرتبة المراد تعديلها.",
                    lambda s: int(s)
                )
                if day is None:
                    return

                rank = next(
                    (r for r in ranks if int(r["days"]) == day),
                    None
                )
                if not rank:
                    return await interaction.followup.send(
                        "ما لقيت رتبة بهذا العدد.",
                        ephemeral=True
                    )

            else:
                rank = {"days": 0, "name": "New Rank", "role_id": 0}
                ranks.append(rank)

            days = await prompt_admin(
                interaction,
                "أرسل عدد الأيام.",
                lambda s: max(1, int(s))
            )
            if days is None:
                return

            name = await prompt_admin(interaction, "أرسل اسم الرتبة.")
            if name is None:
                return

            roleid = await prompt_admin(
                interaction,
                "أرسل Role ID أو 0.",
                lambda s: max(0, int(s))
            )
            if roleid is None:
                return

            if roleid and guild.get_role(roleid) is None:
                return await interaction.followup.send(
                    "ما لقيت الرتبة بهذا الـID داخل السيرفر. "
                    "لم يتم حفظ التعديل.",
                    ephemeral=True
                )

            rank.update({"days": days, "name": name, "role_id": roleid})
            ranks.sort(key=lambda x: int(x["days"]))

            # Keep rank days unique; latest entry wins.
            unique_ranks = {}
            for item in ranks:
                unique_ranks[int(item["days"])] = item

            ranks = sorted(unique_ranks.values(), key=lambda x: int(x["days"]))
            set_config("ranks", ranks)
            await log_admin(guild, admin, f"Edited ranks ({days})")
            await interaction.followup.send("تم حفظ الرتبة.", ephemeral=True)
                elif section == "features":
        toggles = [
            ("DM الصور", "dm_photo_enabled"),
            ("DM البداية", "dm_start_enabled"),
            ("DM الرتب", "dm_rank_enabled"),
            ("الرياكشن", "reaction_enabled"),
            ("الفاصل", "divider_enabled"),
            ("تخصيص الأعضاء", "customization_enabled"),
            ("سجل الصور", "log_photos_enabled"),
            ("سجل الحذف", "log_deletes_enabled"),
            ("سجل الرياكشن", "log_reactions_enabled"),
            ("سجل الخاص", "log_dms_enabled"),
            ("سجل الدروع", "log_shields_enabled"),
            ("سجل الإدارة", "log_admin_enabled"),
            ("إزالة رتب الستريك عند الانقطاع", "remove_rank_roles_on_break"),
            ("الاحتفاظ بالرتب القديمة", "keep_old_rank_roles")
        ]

        await interaction.followup.send(
            "أرسل اسم النظام لتبديل حالته:\n"
            + "\n".join(
                f"{label} — {'مفعل' if get_config(key) else 'متوقف'}"
                for label, key in toggles
            ),
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        key = next(
            (k for label, k in toggles if label == msg.content.strip()),
            None
        )

        if key:
            set_config(key, not get_config(key))
            await log_admin(guild, admin, f"Toggled feature {key}")
            await interaction.followup.send("تم تبديل حالة النظام.", ephemeral=True)
        else:
            await interaction.followup.send("الاسم غير صحيح.", ephemeral=True)

    elif section == "member":
        uid = await prompt_admin(
            interaction,
            "أرسل ID العضو الذي تريد تخصيصه.",
            lambda s: int(s)
        )
        if uid is None:
            return

        m = get_member(uid)
        await interaction.followup.send(
            f"التخصيص الحالي:\n"
            f"Emoji: {m.get('personal_emoji') or 'افتراضي'}\n"
            f"Title: {m.get('personal_title') or 'بدون'}\n"
            f"Show title: {bool(m.get('show_title'))}\n\n"
            "أرسل `emoji` أو `لقب` أو `إظهار` أو `إخفاء` أو `reset`.",
            ephemeral=True
        )

        def check(msg):
            return msg.author.id == admin.id and msg.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return

        action = msg.content.strip()

        if action == "emoji":
            val = await prompt_admin(interaction, "أرسل الإيموجي.")
            if val is not None:
                update_member(uid, personal_emoji=val)

        elif action == "لقب":
            val = await prompt_admin(
                interaction,
                "أرسل اللقب (اكتب `مسح` لإزالته)."
            )
            if val is not None:
                update_member(
                    uid,
                    personal_title=None if val == "مسح" else val[:80]
                )

        elif action == "إظهار":
            update_member(uid, show_title=1)

        elif action == "إخفاء":
            update_member(uid, show_title=0)

        elif action == "reset":
            update_member(
                uid,
                personal_emoji=None,
                personal_title=None,
                show_title=1,
                style="classic"
            )

        await log_admin(guild, admin, f"Customized member {uid}: {action}")
        await interaction.followup.send("تم تنفيذ التخصيص.", ephemeral=True)

    elif section == "reset":
        await interaction.followup.send(
            "⚠️ اكتب `تأكيد` لإرجاع إعدادات البوت الافتراضية. "
            "هذا لا يمس بيانات الستريكات.",
            ephemeral=True
        )

        def check(m):
            return m.author.id == admin.id and m.channel.id == interaction.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=90)
        except asyncio.TimeoutError:
            return

        if msg.content.strip() == "تأكيد":
            for k, v in DEFAULT_CONFIG.items():
                set_config(k, v)
            await log_admin(guild, admin, "Reset bot config to defaults")
            await interaction.followup.send(
                "تمت إعادة إعدادات النظام.",
                ephemeral=True
            )
        else:
            await interaction.followup.send("تم إلغاء العملية.", ephemeral=True)


@bot.command(name="st")
async def admin_panel(ctx):
    if not is_admin(ctx.author):
        return await ctx.send("هذا الأمر للإدارة فقط.")

    view = AdminPanel(ctx.author.id)
    await ctx.send(
        embed=embed(
            "🛠️ ST Control Center",
            "لوحة إدارة الستريك\nاختر القسم من القائمة. التغييرات تحفظ مباشرة."
        ),
        view=view
    )


@bot.command(name="تخصيص")
async def admin_custom(ctx, member: discord.Member):
    if not is_admin(ctx.author):
        return await ctx.send("هذا الأمر للإدارة فقط.")

    if not get_config("customization_enabled"):
        return await ctx.send("التخصيصات متوقفة من لوحة `-st`.")

    lock = ADMIN_INPUT_LOCKS.setdefault(ctx.author.id, asyncio.Lock())
    if lock.locked():
        return await ctx.send(
            "عندك جلسة إعدادات مفتوحة. خلّصها أولًا أو اكتب `إلغاء`."
        )

    await lock.acquire()
    try:
        await ctx.send(
            embed=embed(
                "🛠️ تخصيص عضو",
                f"العضو: {member.mention}\n"
                "أرسل أحد الخيارات: `emoji` / `لقب` / `إظهار` / `إخفاء` / `reset`"
            )
        )

        def check(m):
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await bot.wait_for("message", check=check, timeout=150)
        except asyncio.TimeoutError:
            return await ctx.send("انتهى وقت التخصيص.")

        action = msg.content.strip().lower()

        if action == "emoji":
            val = await prompt_admin(ctx, "أرسل الإيموجي الذي تريده.")
            if val is not None:
                update_member(member.id, personal_emoji=val)
                await log_admin(ctx.guild, ctx.author, f"Set emoji for member {member.id}")

        elif action in ("لقب", "title"):
            val = await prompt_admin(ctx, "أرسل اللقب (اكتب `مسح` لإزالته).")
            if val is not None:
                update_member(
                    member.id,
                    personal_title=None if val.strip() == "مسح" else val[:80]
                )
                await log_admin(ctx.guild, ctx.author, f"Set title for member {member.id}")

        elif action == "إظهار":
            update_member(member.id, show_title=1)
            await log_admin(ctx.guild, ctx.author, f"Show title for member {member.id}")

        elif action == "إخفاء":
            update_member(member.id, show_title=0)
            await log_admin(ctx.guild, ctx.author, f"Hide title for member {member.id}")

        elif action == "reset":
            update_member(
                member.id,
                personal_emoji=None,
                personal_title=None,
                show_title=1,
                style="classic"
            )
            await log_admin(ctx.guild, ctx.author, f"Reset customization for member {member.id}")

        else:
            await ctx.send("الاختيار غير صحيح، ألغيت التخصيص.")

    finally:
        if lock.locked():
            lock.release()


# ------------------------------ Events ------------------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Handle multiword command before normal prefix parsing.
    if message.guild and message.content.strip().lower() == "-سجل ستريكات":
        ctx = await bot.get_context(message)
        await streak_history_command(ctx)
        return

    streak_channel = int(get_config("streak_channel_id") or 0)

    if message.guild and message.channel.id == streak_channel and streak_channel:
        images = [
            a for a in message.attachments
            if (a.content_type or "").lower().startswith("image/")
            or a.filename.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
            )
        ]

        if images and isinstance(message.author, discord.Member):
            try:
                await record_photo(message, images)
            except Exception:
                log.exception("Photo processing failed for message %s", message.id)

    await bot.process_commands(message)


@bot.event
async def on_message_delete(message):
    streak_channel_id = int(get_config("streak_channel_id") or 0)

    if (
        not message.guild
        or not message.attachments
        or not streak_channel_id
        or message.channel.id != streak_channel_id
    ):
        return

    deleted_images = [
        a for a in message.attachments
        if (a.content_type or "").lower().startswith("image/")
        or a.filename.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        )
    ]

    if not deleted_images or not get_config("log_deletes_enabled"):
        return

    ch = channel_by_id(message.guild, get_config("log_delete_channel_id"))
    if not ch:
        return

    files = []
    for a in deleted_images:
        f = await save_image_for_log(a)
        if f:
            files.append(f)

    fallback_urls = [a.url for a in deleted_images if not a.url.startswith("attachment://")]

    e = embed(
        "🗑️ Deleted Streak Photo",
        f"**Member:** {getattr(message.author, 'mention', 'Unknown')} "
        f"(`{getattr(message.author, 'id', '?')}`)\n"
        f"**Channel:** {message.channel.mention}\n"
        f"**Message:** {message.id}\n"
        f"**Time:** {now_iso()}\n"
        + (
            "\n**Attachment links (fallback):**\n" + "\n".join(fallback_urls)
            if fallback_urls else ""
        )
    )

    try:
        await ch.send(embed=e, files=files or None)
    except Exception as ex:
        log.warning("Delete log failed: %s", ex)


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or not reaction.message.guild:
        return
    if not get_config("log_reactions_enabled"):
        return

    ch = channel_by_id(
        reaction.message.guild,
        get_config("log_reaction_channel_id")
    )
    if not ch:
        return

    owner = reaction.message.author
    with db_connect() as c:
        c.execute(
            "INSERT INTO reaction_logs(reactor_id,owner_id,message_id,emoji,action,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                user.id,
                getattr(owner, "id", 0),
                reaction.message.id,
                str(reaction.emoji),
                "add",
                now_iso()
            )
        )

    e = embed(
        "➕ Reaction Added",
        f"**Reacted by:** {user.mention} (`{user.id}`)\n"
        f"**Image owner:** {owner.mention if owner else 'Unknown'}\n"
        f"**Emoji:** {reaction.emoji}\n"
        f"**Message:** {reaction.message.jump_url}"
    )

    try:
        await ch.send(embed=e)
    except Exception:
        pass


@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot or not reaction.message.guild:
        return
    if not get_config("log_reactions_enabled"):
        return

    ch = channel_by_id(
        reaction.message.guild,
        get_config("log_reaction_channel_id")
    )
    if not ch:
        return

    owner = reaction.message.author
    with db_connect() as c:
        c.execute(
            "INSERT INTO reaction_logs(reactor_id,owner_id,message_id,emoji,action,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                user.id,
                getattr(owner, "id", 0),
                reaction.message.id,
                str(reaction.emoji),
                "remove",
                now_iso()
            )
        )

    e = embed(
        "➖ Reaction Removed",
        f"**Removed by:** {user.mention} (`{user.id}`)\n"
        f"**Image owner:** {owner.mention if owner else 'Unknown'}\n"
        f"**Emoji:** {reaction.emoji}\n"
        f"**Message:** {reaction.message.jump_url}"
    )

    try:
        await ch.send(embed=e)
    except Exception:
        pass


@bot.event
async def on_ready():
    log.info("Logged in as %s (%s)", bot.user, bot.user.id)
    for guild in bot.guilds:
        log.info("Ready in guild %s (%s)", guild.name, guild.id)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send("استخدام الأمر غير صحيح. استخدم `-اوامر`.")

    if isinstance(error, commands.BadArgument):
        return await ctx.send("ما قدرت أحدد العضو. اذكر العضو أو استخدم ID صحيح.")

    log.exception("Command error", exc_info=error)
    try:
        await ctx.send("صار خطأ أثناء تنفيذ الأمر. تم تسجيله للمراجعة.")
    except Exception:
        pass


bot.run(TOKEN)
