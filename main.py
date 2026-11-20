# -*- coding: utf-8 -*-
import os
import sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

# =========================
# إعدادات أساسية
# =========================

TZ = ZoneInfo("Asia/Kuwait")
TOKEN = os.getenv("DISCORD_TOKEN")

DB = sqlite3.connect("streaks.db", check_same_thread=False)
DB.row_factory = sqlite3.Row

DB.executescript("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    current_streak INTEGER DEFAULT 0,
    longest_streak INTEGER DEFAULT 0,
    photos INTEGER DEFAULT 0,
    streak_photos INTEGER DEFAULT 0,
    last_day TEXT,
    reminder_day TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    photos INTEGER DEFAULT 0,
    PRIMARY KEY(user_id, day)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
""")

# ترقية قاعدة البيانات القديمة تلقائياً
columns = [r["name"] for r in DB.execute("PRAGMA table_info(users)").fetchall()]
if "streak_photos" not in columns:
    DB.execute("ALTER TABLE users ADD COLUMN streak_photos INTEGER DEFAULT 0")

DB.commit()


# =========================
# أدوات الإعدادات
# =========================

def cfg(key, default=""):
    row = DB.execute(
        "SELECT value FROM settings WHERE key=?",
        (key,)
    ).fetchone()
    return row["value"] if row else default


def setcfg(key, value):
    DB.execute("""
        INSERT INTO settings(key,value)
        VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, str(value)))
    DB.commit()


def enabled(key, default="1"):
    return cfg(key, default).lower() in ("1", "on", "yes", "true")


def today():
    return datetime.now(TZ).date()


def userrow(uid):
    return DB.execute(
        "SELECT * FROM users WHERE user_id=?",
        (uid,)
    ).fetchone()


def active(row):
    if not row or not row["last_day"]:
        return 0

    last = date.fromisoformat(row["last_day"])

    if last >= today() - timedelta(days=1):
        return row["current_streak"]

    return 0


def is_image(att):
    return (
        (att.content_type or "").startswith("image/")
        or att.filename.lower().endswith(
            (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        )
    )


def get_reactions():
    raw = cfg("reactions", "🔥,❤️,💯")
    return [x.strip() for x in raw.split(",") if x.strip()]


# =========================
# تسجيل الصور والستريك
# =========================

def add_photos(uid, count):
    d = today()
    ds = d.isoformat()
    row = userrow(uid)
    new_streak = False

    if not row:
        streak = 1
        longest = 1
        total = count
        streak_photos = count
        new_streak = True

        DB.execute("""
            INSERT INTO users(
                user_id,current_streak,longest_streak,
                photos,streak_photos,last_day
            )
            VALUES(?,?,?,?,?,?)
        """, (uid, streak, longest, total, streak_photos, ds))

    else:
        last = date.fromisoformat(row["last_day"]) if row["last_day"] else None

        if last == d:
            streak = row["current_streak"]
            total = row["photos"] + count
            streak_photos = row["streak_photos"] + count

        elif last == d - timedelta(days=1):
            streak = row["current_streak"] + 1
            total = row["photos"] + count
            streak_photos = row["streak_photos"] + count

        else:
            streak = 1
            total = count
            streak_photos = count
            new_streak = True

        longest = max(row["longest_streak"], streak)

        DB.execute("""
            UPDATE users
            SET current_streak=?,longest_streak=?,
                photos=?,streak_photos=?,last_day=?
            WHERE user_id=?
        """, (
            streak, longest, total, streak_photos, ds, uid
        ))

    DB.execute("""
        INSERT INTO activity(user_id,day,photos)
        VALUES(?,?,?)
        ON CONFLICT(user_id,day)
        DO UPDATE SET photos=photos+excluded.photos
    """, (uid, ds, count))

    DB.commit()
    return streak, longest, total, streak_photos, new_streak


# =========================
# البوت
# =========================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="-",
    intents=intents,
    help_command=None
)


async def send_dm(member, text):
    text = (
        text.replace("{user}", member.display_name)
        .replace("{mention}", member.mention)
    )

    try:
        await member.send(text)
    except (discord.Forbidden, discord.HTTPException):
        pass


# =========================
# لوحة التحكم
# =========================

@bot.command(name="st")
@commands.has_guild_permissions(manage_guild=True)
async def control_panel(ctx):
    embed = discord.Embed(
        title="🔥 لوحة تحكم الستريك",
        description=(
            "اختاري الزر المطلوب لتعديل إعدادات البوت.\n"
            "جميع أدوات التحكم في هذه اللوحة خاصة بالإدارة."
        ),
        color=discord.Color.orange()
    )

    await ctx.send(embed=embed, view=AdminPanel())


class AdminPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="تحديد قناة الستريك", style=discord.ButtonStyle.primary)
    async def channel(self, interaction, button):
        await interaction.response.send_modal(ChannelModal())

    @discord.ui.button(label="الإيموجيات", style=discord.ButtonStyle.primary)
    async def emoji(self, interaction, button):
        await interaction.response.send_modal(EmojiModal())

    @discord.ui.button(label="الرسائل الخاصة", style=discord.ButtonStyle.primary)
    async def messages(self, interaction, button):
        await interaction.response.send_modal(MessagesModal())

    @discord.ui.button(label="صورة الفاصل", style=discord.ButtonStyle.primary)
    async def divider(self, interaction, button):
        await interaction.response.send_modal(DividerModal())

    @discord.ui.button(label="وقت التذكير", style=discord.ButtonStyle.primary)
    async def reminder_settings(self, interaction, button):
        await interaction.response.send_modal(ReminderModal())

    @discord.ui.button(label="تشغيل / تعطيل الميزات", style=discord.ButtonStyle.secondary)
    async def toggles(self, interaction, button):
        await interaction.response.send_modal(ToggleModal())

    @discord.ui.button(label="إعلان للمشاركين", style=discord.ButtonStyle.danger)
    async def broadcast(self, interaction, button):
        await interaction.response.send_modal(BroadcastModal())

    @discord.ui.button(label="اختبار البوت", style=discord.ButtonStyle.success)
    async def test(self, interaction, button):
        await interaction.response.send_message(
            f"البوت يعمل بنجاح ✅\n{bot.user}",
            ephemeral=True
        )

    async def interaction_check(self, interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "هذه اللوحة للإدارة فقط.",
                ephemeral=True
            )
            return False
        return True


# =========================
# إعداد القناة
# =========================

class ChannelModal(discord.ui.Modal, title="تحديد قناة الستريك"):
    channel_id = discord.ui.TextInput(
        label="ID قناة الستريك",
        placeholder="أدخل رقم القناة فقط",
        required=True,
        max_length=25
    )

    async def on_submit(self, interaction):
        value = self.channel_id.value.strip()

        if not value.isdigit():
            return await interaction.response.send_message(
                "رقم القناة غير صحيح.",
                ephemeral=True
            )

        setcfg("channel_id", value)

        await interaction.response.send_message(
            "تم حفظ قناة الستريك ✅",
            ephemeral=True
        )


# =========================
# إعداد الإيموجيات
# =========================

class EmojiModal(discord.ui.Modal, title="تعديل الإيموجيات"):
    values = discord.ui.TextInput(
        label="الإيموجيات (افصل بينها بفاصلة)",
        placeholder="🔥, ❤️, 💯",
        required=True,
        max_length=300
    )

    async def on_submit(self, interaction):
        setcfg("reactions", self.values.value)

        await interaction.response.send_message(
            "تم حفظ الإيموجيات ✅",
            ephemeral=True
        )


# =========================
# الرسائل الخاصة
# =========================

class MessagesModal(discord.ui.Modal, title="تعديل الرسائل الخاصة"):
    start = discord.ui.TextInput(
        label="رسالة بداية الستريك",
        default=cfg("dm_start", "بديت ستريك يا وحش استمر 🔥"),
        max_length=500
    )

    photo = discord.ui.TextInput(
        label="رسالة بعد كل صورة",
        default=cfg(
            "dm_photo",
            "تم تسجيل صورتك! ستريكك الحالي: {streak} يوم 🔥"
        ),
        max_length=500
    )

    reminder_text = discord.ui.TextInput(
        label="رسالة التذكير",
        default=cfg(
            "dm_reminder",
            "⏰ لا تنسى الستريك اليوم! باقي وقت على نهاية اليوم 🔥"
        ),
        max_length=500
    )

    async def on_submit(self, interaction):
        setcfg("dm_start", self.start.value)
        setcfg("dm_photo", self.photo.value)
        setcfg("dm_reminder", self.reminder_text.value)

        await interaction.response.send_message(
            "تم حفظ الرسائل الخاصة ✅",
            ephemeral=True
        )


# =========================
# صورة الفاصل
# =========================

class DividerModal(discord.ui.Modal, title="صورة الفاصل"):
    url = discord.ui.TextInput(
        label="رابط الصورة",
        placeholder="https://...",
        required=False,
        max_length=1000
    )

    async def on_submit(self, interaction):
        value = self.url.value.strip()

        if value and not value.startswith(("https://", "http://")):
            return await interaction.response.send_message(
                "الرابط غير صحيح. يجب أن يبدأ بـ https://",
                ephemeral=True
            )

        setcfg("divider_url", value)

        await interaction.response.send_message(
            "تم حفظ رابط الفاصل. اتركيه فارغًا لإلغاء الصورة.",
            ephemeral=True
        )


# =========================
# إعداد التذكير
# =========================

class ReminderModal(discord.ui.Modal, title="إعداد التذكير"):
    hour = discord.ui.TextInput(
        label="ساعة التذكير بتوقيت الكويت (0-23)",
        default=cfg("reminder_hour", "19"),
        max_length=2
    )

    async def on_submit(self, interaction):
        try:
            h = int(self.hour.value)

            if not 0 <= h <= 23:
                raise ValueError

            setcfg("reminder_hour", h)

            await interaction.response.send_message(
                f"تم ضبط التذكير على {h:02}:00 بتوقيت الكويت ✅",
                ephemeral=True
            )

        except ValueError:
            await interaction.response.send_message(
                "أدخل ساعة صحيحة من 0 إلى 23.",
                ephemeral=True
            )


# =========================
# تشغيل وتعطيل الميزات
# =========================

class ToggleModal(discord.ui.Modal, title="تشغيل / تعطيل"):
    dm = discord.ui.TextInput(
        label="الرسائل الخاصة (on/off)",
        default=cfg("feature_dm", "on"),
        max_length=3
    )

    reminder = discord.ui.TextInput(
        label="التذكيرات (on/off)",
        default=cfg("feature_reminder", "on"),
        max_length=3
    )

    reactions = discord.ui.TextInput(
        label="التفاعلات (on/off)",
        default=cfg("feature_reactions", "on"),
        max_length=3
    )

    async def on_submit(self, interaction):
        items = [
            ("feature_dm", self.dm.value),
            ("feature_reminder", self.reminder.value),
            ("feature_reactions", self.reactions.value)
        ]

        for key, value in items:
            value = value.lower().strip()
            setcfg(
                key,
                "1" if value in ("on", "1", "yes") else "0"
            )

        await interaction.response.send_message(
            "تم تحديث إعدادات الميزات ✅\n"
            "استخدم on للتشغيل أو off للإيقاف.",
            ephemeral=True
        )


# =========================
# إعلان للمشاركين
# =========================

class BroadcastModal(discord.ui.Modal, title="إرسال إعلان"):
    message_text = discord.ui.TextInput(
        label="نص الإعلان",
        style=discord.TextStyle.paragraph,
        max_length=1800
    )

    async def on_submit(self, interaction):
        await interaction.response.send_message(
            "بدأ إرسال الإعلان إلى أعضاء الستريك…",
            ephemeral=True
        )

        count = 0

        rows = DB.execute(
            "SELECT user_id FROM users"
        ).fetchall()

        for row in rows:
            try:
                user = await bot.fetch_user(row["user_id"])
                await user.send(self.message_text.value)
                count += 1
            except (discord.Forbidden, discord.HTTPException):
                pass

        await interaction.followup.send(
            f"انتهى الإرسال. وصل الإعلان إلى {count} عضو.",
            ephemeral=True
        )


# =========================
# أمر ستريكي
# =========================

@bot.command(name="ستريكي")
async def streak(ctx):
    row = userrow(ctx.author.id)

    if not row:
        return await ctx.reply("ما عندك ستريك مسجل للحين 📸")

    current = active(row)

    embed = discord.Embed(
        title=f"📊 ستريك {ctx.author.display_name}",
        color=discord.Color.orange()
    )

    embed.set_thumbnail(url=ctx.author.display_avatar.url)

    embed.add_field(
        name="🔥 الحالي",
        value=f"{current} يوم",
        inline=True
    )

    embed.add_field(
        name="🏆 الأطول",
        value=f"{row['longest_streak']} يوم",
        inline=True
    )

    embed.add_field(
        name="📸 صور الستريك الحالي",
        value=str(row["streak_photos"] if current else 0),
        inline=True
    )

    embed.add_field(
        name="📷 مجموع الصور",
        value=str(row["photos"]),
        inline=True
    )

    await ctx.reply(embed=embed)


# =========================
# الترتيب
# =========================

async def leaderboard(ctx, period="all"):
    if period == "all":
        rows = DB.execute("""
            SELECT user_id,longest_streak AS val
            FROM users
            ORDER BY val DESC
            LIMIT 10
        """).fetchall()

        title = "🏆 أطول ستريكات"

    else:
        days = 7 if period == "week" else 30
        since = (today() - timedelta(days=days - 1)).isoformat()

        rows = DB.execute("""
            SELECT user_id,SUM(photos) AS val
            FROM activity
            WHERE day>=?
            GROUP BY user_id
            ORDER BY val DESC
            LIMIT 10
        """, (since,)).fetchall()

        title = "📸 أكثر الصور خلال " + (
            "آخر 7 أيام" if period == "week" else "آخر 30 يوم"
        )

    if not rows:
        return await ctx.reply("ما فيه بيانات كافية للحين.")

    lines = []

    for i, row in enumerate(rows, 1):
        try:
            member = await ctx.guild.fetch_member(row["user_id"])
            name = member.display_name
        except:
            name = f"عضو {row['user_id']}"

        unit = "يوم" if period == "all" else "صورة"
        lines.append(f"**{i}.** {name} — **{row['val']} {unit}**")

    await ctx.reply(title + "\n" + "\n".join(lines))


@bot.command(name="توب_ستريك")
async def top_streak(ctx):
    await leaderboard(ctx, "all")


@bot.command(name="tops")
async def tops(ctx):
    await leaderboard(ctx, "all")


@bot.command(name="توب_صور")
async def top_photos(ctx):
    await leaderboard(ctx, "month")


@bot.command(name="توب_أسبوع")
async def top_week(ctx):
    await leaderboard(ctx, "week")


# =========================
# استقبال الصور
# =========================

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    channel_id = int(cfg("channel_id", "0") or 0)

    if message.guild and channel_id and message.channel.id == channel_id:
        pictures = [
            att for att in message.attachments
            if is_image(att)
        ]

        if pictures:
            streak, longest, total, streak_photos, new_streak = add_photos(
                message.author.id,
                len(pictures)
            )

            # فاصل الستريك الجديد
            if new_streak:
                divider_url = cfg("divider_url", "")

                if divider_url:
                    embed = discord.Embed(color=discord.Color.orange())
                    embed.set_image(url=divider_url)

                    try:
                        await message.channel.send(embed=embed)
                    except discord.HTTPException:
                        await message.channel.send(
                            "━━━━━━━━ 🔥 بداية ستريك جديد 🔥 ━━━━━━━━"
                        )
                else:
                    await message.channel.send(
                        "━━━━━━━━ 🔥 بداية ستريك جديد 🔥 ━━━━━━━━"
                    )

                # رسالة بداية الستريك
                if enabled("feature_dm"):
                    await send_dm(
                        message.author,
                        cfg("dm_start", "بديت ستريك يا وحش استمر 🔥")
                    )

            # التفاعلات
            if enabled("feature_reactions"):
                for emoji in get_reactions()[:3]:
                    try:
                        await message.add_reaction(emoji)
                    except discord.HTTPException:
                        pass

            # رسالة بعد كل صورة
            if enabled("feature_dm"):
                text = cfg(
                    "dm_photo",
                    "تم تسجيل صورتك! ستريكك الحالي: {streak} يوم 🔥"
                )

                text = (
                    text.replace("{streak}", str(streak))
                    .replace("{photos}", str(streak_photos))
                    .replace("{total}", str(total))
                    .replace("{longest}", str(longest))
                )

                await send_dm(message.author, text)

    await bot.process_commands(message)


# =========================
# تذكير يومي
# =========================

@tasks.loop(minutes=1)
async def reminder():
    if not enabled("feature_reminder"):
        return

    now = datetime.now(TZ)
    hour = int(cfg("reminder_hour", "19"))

    if now.hour < hour:
        return

    day = now.date().isoformat()
    yesterday = (now.date() - timedelta(days=1)).isoformat()

    rows = DB.execute(
        "SELECT * FROM users WHERE last_day=?",
        (yesterday,)
    ).fetchall()

    for row in rows:
        if row["reminder_day"] == day:
            continue

        try:
            user = await bot.fetch_user(row["user_id"])

            await send_dm(
                user,
                cfg(
                    "dm_reminder",
                    "⏰ لا تنسى الستريك اليوم! باقي وقت على نهاية اليوم 🔥"
                )
            )

        except (discord.Forbidden, discord.HTTPException):
            pass

        DB.execute(
            "UPDATE users SET reminder_day=? WHERE user_id=?",
            (day, row["user_id"])
        )

    DB.commit()


@reminder.before_loop
async def wait_ready():
    await bot.wait_until_ready()


# =========================
# منع خطأ CommandNotFound
# =========================

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("هذا الأمر للإدارة فقط 🔒")
        return

    if isinstance(error, commands.CommandInvokeError):
        print("Command error:", repr(error.original))
        await ctx.reply("صار خطأ أثناء تنفيذ الأمر. تم تسجيل الخطأ.")
        return

    print("Command error:", repr(error))


# =========================
# تشغيل
# =========================

@bot.event
async def on_ready():
    print(f"Bot online: {bot.user}")

    if not reminder.is_running():
        reminder.start()


if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")

bot.run(TOKEN)
