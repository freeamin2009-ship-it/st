# ==============================
# ST - STREAK BOT
# ==============================

import os
import re
import json
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks


# ==============================
# CONFIG
# ==============================

TOKEN = os.getenv("DISCORD_TOKEN")

PREFIX = "-"
DB_PATH = os.getenv("DB_PATH", "/data/streaks.sqlite3")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(
    command_prefix=PREFIX,
    intents=intents,
    help_command=None
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ST")


# ==============================
# DATABASE
# ==============================

def db():
    global DB_PATH

    paths = [
        DB_PATH,
        os.path.join(os.getcwd(), "streaks.sqlite3"),
        "/tmp/streaks.sqlite3",
    ]

    last_error = None

    for path in dict.fromkeys(paths):
        try:
            folder = os.path.dirname(path)

            if folder:
                os.makedirs(folder, exist_ok=True)

            conn = sqlite3.connect(path, timeout=30)
            conn.row_factory = sqlite3.Row

            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")

            DB_PATH = path
            return conn

        except (sqlite3.OperationalError, OSError) as error:
            last_error = error
            logger.warning(
                "Database path unavailable: %s | %s",
                path,
                error
            )

    raise RuntimeError(
        f"Cannot open database: {last_error}"
    )


def init_db():
    with db() as conn:

        # إعدادات السيرفر
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (guild_id, key)
            )
        """)

        # بيانات الستريكات
        conn.execute("""
            CREATE TABLE IF NOT EXISTS streaks (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                streak INTEGER DEFAULT 0,
                best_streak INTEGER DEFAULT 0,
                photos INTEGER DEFAULT 0,
                last_photo TEXT,
                started_at TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        # الدروع
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shields (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                amount INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        # تخصيص العضو
        conn.execute("""
            CREATE TABLE IF NOT EXISTS customizations (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                emoji TEXT,
                nickname TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        # الرتب
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_settings (
                guild_id INTEGER NOT NULL,
                rank_type TEXT NOT NULL,
                role_id INTEGER,
                PRIMARY KEY (guild_id, rank_type)
            )
        """)

        # إعداد الرياكشن
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                guild_id INTEGER NOT NULL,
                emoji TEXT NOT NULL
            )
        """)

        # قنوات الرياكشن
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reaction_channels (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            )
        """)

        # تذكيرات الستريك
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                remind_at TEXT NOT NULL,
                sent INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        """)

        conn.commit()


# ==============================
# SETTINGS HELPERS
# ==============================

def get_setting(guild_id, key, default=None):

    with db() as conn:
        row = conn.execute(
            """
            SELECT value
            FROM settings
            WHERE guild_id=? AND key=?
            """,
            (guild_id, key)
        ).fetchone()

    if row is None:
        return default

    return row["value"]


def set_setting(guild_id, key, value):

    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings (guild_id, key, value)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, key)
            DO UPDATE SET value=excluded.value
            """,
            (guild_id, key, str(value))
        )

        conn.commit()


def get_channel_id(guild_id, key):

    value = get_setting(guild_id, key)

    if not value:
        return None

    try:
        return int(value)
    except ValueError:
        return None


def get_config(guild_id):

    with db() as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE guild_id=?",
            (guild_id,)
        ).fetchall()

    return {
        row["key"]: row["value"]
        for row in rows
    }


# ==============================
# ADMIN CHECK
# ==============================

def is_admin(member):

    if member.guild_permissions.administrator:
        return True

    return member.guild_permissions.manage_guild


async def admin_check(ctx):

    if not is_admin(ctx.author):
        await ctx.reply(
            "❌ هذا الأمر للإدارة فقط.",
            delete_after=8
        )
        return False

    return True


# ==============================
# BOT READY
# ==============================

@bot.event
async def on_ready():

    init_db()

    logger.info(
        "ST connected as %s (%s)",
        bot.user,
        bot.user.id
    )

    print(f"ST is ready: {bot.user}")
    # ==============================
# CHANNEL CONFIGURATION
# ==============================

CHANNEL_OPTIONS = [
    ("streak_channel", "قناة الستريك"),
    ("general_log", "قناة السجل"),
    ("photo_log", "قناة لوق الصور"),
    ("reaction_log", "قناة لوق الرياكشن"),
    ("delete_log", "قناة لوق الحذف"),
    ("rank_log", "قناة الرتب"),
]


def channel_name(key):

    names = dict(CHANNEL_OPTIONS)
    return names.get(key, key)


class ChannelIDModal(discord.ui.Modal):

    def __init__(self, guild_id, channel_key):
        super().__init__(
            title=f"تحديد {channel_name(channel_key)}"
        )

        self.guild_id = guild_id
        self.channel_key = channel_key

        self.channel_id_input = discord.ui.TextInput(
            label="أرسل Channel ID",
            placeholder="الصق رقم القناة هنا",
            required=True,
            max_length=25
        )

        self.add_item(self.channel_id_input)

    async def on_submit(self, interaction):

        raw = self.channel_id_input.value.strip()

        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ الـ ID لازم يكون أرقام فقط.",
                ephemeral=True
            )
            return

        channel_id = int(raw)

        channel = interaction.guild.get_channel(channel_id)

        if channel is None:
            await interaction.response.send_message(
                "⚠️ ما قدرت ألقى قناة بهذا الـ ID "
                "داخل السيرفر.",
                ephemeral=True
            )
            return

        set_setting(
            self.guild_id,
            self.channel_key,
            channel_id
        )

        await interaction.response.send_message(
            f"✅ تم حفظ {channel_name(self.channel_key)}:\n"
            f"{channel.mention}",
            ephemeral=True
        )


class ChannelSelect(discord.ui.Select):

    def __init__(self, guild_id):

        self.guild_id = guild_id

        options = [
            discord.SelectOption(
                label=name,
                value=key
            )
            for key, name in CHANNEL_OPTIONS
        ]

        super().__init__(
            placeholder="اختر القناة التي تريد تحديدها...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction):

        key = self.values[0]

        await interaction.response.send_modal(
            ChannelIDModal(
                interaction.guild.id,
                key
            )
        )


class ChannelPanel(discord.ui.View):

    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.add_item(ChannelSelect(guild_id))


# ==============================
# ADMIN MAIN PANEL
# ==============================

ADMIN_OPTIONS = [
    ("channels", "القنوات", "تحديد قنوات البوت"),
    ("messages", "الرسائل", "تعديل رسائل البوت"),
    ("announcements", "الإعلانات", "إرسال إعلان"),
    ("ranks", "الرتب", "إعداد رتب الستريك"),
    ("shields", "الدروع", "إعداد نظام الدروع"),
    ("custom", "التخصيص", "تخصيص الستريك للأعضاء"),
    ("reactions", "رياكشن", "إعداد الرياكشن التلقائي"),
    ("divider", "فاصل", "إعداد فاصل الصور"),
]


class AdminSelect(discord.ui.Select):

    def __init__(self):

        options = [
            discord.SelectOption(
                label=title,
                value=value,
                description=description
            )
            for value, title, description in ADMIN_OPTIONS
        ]

        super().__init__(
            placeholder="اختر قسم الإدارة...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction):

        section = self.values[0]

        if section == "channels":

            await interaction.response.send_message(
                "📡 **إعداد القنوات**\n"
                "اختر نوع القناة، ثم أرسل الـ ID.",
                view=ChannelPanel(interaction.guild.id),
                ephemeral=True
            )

        else:

            await open_admin_section(
                interaction,
                section
            )


class AdminPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AdminSelect())


# ==============================
# ADMIN PANEL COMMAND
# ==============================

@bot.command(name="st")
async def admin_panel(ctx):

    if not await admin_check(ctx):
        return

    embed = discord.Embed(
        title="🛠️ ST Control Center",
        description=(
            "**ST ADMIN PANEL**\n\n"
            "اختر القسم الذي تريد التحكم فيه "
            "من القائمة بالأسفل."
        ),
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="القنوات",
        value="تحديد قنوات البوت",
        inline=True
    )

    embed.add_field(
        name="الرسائل",
        value="تعديل رسائل البوت",
        inline=True
    )

    embed.add_field(
        name="الإعلانات",
        value="إرسال إعلان",
        inline=True
    )

    embed.add_field(
        name="الرتب",
        value="إعداد الرتب",
        inline=True
    )

    embed.add_field(
        name="الدروع",
        value="تشغيل وإعداد الدروع",
        inline=True
    )

    embed.add_field(
        name="التخصيص",
        value="إعداد تخصيص الأعضاء",
        inline=True
    )

    embed.add_field(
        name="رياكشن",
        value="الرياكشن التلقائي",
        inline=True
    )

    embed.add_field(
        name="فاصل",
        value="إعداد فاصل الصور",
        inline=True
    )

    await ctx.send(
        embed=embed,
        view=AdminPanel()
    )


# ==============================
# CHANNEL COMMAND
# ==============================

@bot.command(name="stg")
async def channel_command(ctx):

    if not await admin_check(ctx):
        return

    await ctx.send(
        "📡 **لوحة إعداد القنوات**\n"
        "اختر القناة التي تريد إعدادها:",
        view=ChannelPanel(ctx.guild.id)
    )
    # ==============================
# MESSAGE SETTINGS
# ==============================

MESSAGE_OPTIONS = [
    (
        "start_message",
        "رسالة بداية الستريك",
        "تُرسل عند بداية ستريك جديد"
    ),
    (
        "reminder_message",
        "رسالة تذكير الستريك",
        "تُرسل قبل فقدان الستريك"
    ),
    (
        "photo_message",
        "رسالة الصور",
        "تُرسل عند تسجيل صورة جديدة"
    ),
    (
        "streak_count_message",
        "رسالة عدد الستريكات",
        "رسالة عدد الستريكات"
    ),
]


class TextSettingModal(discord.ui.Modal):

    def __init__(
        self,
        guild_id,
        setting_key,
        setting_title,
        current_value=None
    ):

        super().__init__(title=setting_title)

        self.guild_id = guild_id
        self.setting_key = setting_key

        self.text_input = discord.ui.TextInput(
            label="النص الجديد",
            placeholder="اكتب الرسالة التي تريدها",
            default=current_value or "",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=2000
        )

        self.add_item(self.text_input)

    async def on_submit(self, interaction):

        set_setting(
            self.guild_id,
            self.setting_key,
            self.text_input.value
        )

        await interaction.response.send_message(
            "✅ تم حفظ النص بنجاح.",
            ephemeral=True
        )


class MessageSettingSelect(discord.ui.Select):

    def __init__(self, guild_id):

        self.guild_id = guild_id

        options = [
            discord.SelectOption(
                label=title,
                value=key,
                description=description
            )
            for key, title, description in MESSAGE_OPTIONS
        ]

        super().__init__(
            placeholder="اختر الرسالة التي تريد تعديلها...",
            options=options,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction):

        key = self.values[0]

        title = next(
            item[1]
            for item in MESSAGE_OPTIONS
            if item[0] == key
        )

        current = get_setting(
            self.guild_id,
            key
        )

        await interaction.response.send_modal(
            TextSettingModal(
                self.guild_id,
                key,
                title,
                current
            )
        )


class MessagePanel(discord.ui.View):

    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.add_item(
            MessageSettingSelect(guild_id)
        )


# ==============================
# ANNOUNCEMENT
# ==============================

class AnnouncementModal(discord.ui.Modal):

    def __init__(self, guild_id):

        super().__init__(title="إرسال إعلان")

        self.guild_id = guild_id

        self.announcement = discord.ui.TextInput(
            label="نص الإعلان",
            placeholder="اكتب الإعلان هنا...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000
        )

        self.add_item(self.announcement)

    async def on_submit(self, interaction):

        await interaction.response.send_message(
            "📢 اختر القناة التي تريد إرسال الإعلان فيها "
            "من خلال تحديد قناة الإعلان في إعدادات القنوات.",
            ephemeral=True
        )

        set_setting(
            self.guild_id,
            "announcement_draft",
            self.announcement.value
        )


class AnnouncementButton(discord.ui.Button):

    def __init__(self):
        super().__init__(
            label="كتابة إعلان",
            style=discord.ButtonStyle.primary,
            emoji="📢"
        )

    async def callback(self, interaction):

        await interaction.response.send_modal(
            AnnouncementModal(interaction.guild.id)
        )


class AnnouncementPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(AnnouncementButton())
        # ==============================
# RANKS
# ==============================

RANK_OPTIONS = [
    ("first_streak", "رتبة أول ستريك"),
    ("most_photos", "رتبة أكثر صور"),
    ("longest_streak", "رتبة أطول مدة"),
]


class RoleIDModal(discord.ui.Modal):

    def __init__(self, guild_id, rank_type, title):

        super().__init__(title=title)

        self.guild_id = guild_id
        self.rank_type = rank_type

        self.role_id_input = discord.ui.TextInput(
            label="Role ID",
            placeholder="الصق ID الرتبة",
            required=True,
            max_length=25
        )

        self.add_item(self.role_id_input)

    async def on_submit(self, interaction):

        raw = self.role_id_input.value.strip()

        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ ID الرتبة غير صحيح.",
                ephemeral=True
            )
            return

        role_id = int(raw)

        role = interaction.guild.get_role(role_id)

        if role is None:
            await interaction.response.send_message(
                "❌ ما لقيت الرتبة في السيرفر.",
                ephemeral=True
            )
            return

        with db() as conn:
            conn.execute(
                """
                INSERT INTO rank_settings
                (guild_id, rank_type, role_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, rank_type)
                DO UPDATE SET role_id=excluded.role_id
                """,
                (self.guild_id, self.rank_type, role_id)
            )
            conn.commit()

        await interaction.response.send_message(
            f"✅ تم حفظ الرتبة: {role.mention}",
            ephemeral=True
        )


class RankSelect(discord.ui.Select):

    def __init__(self, guild_id):

        self.guild_id = guild_id

        super().__init__(
            placeholder="اختر نوع الرتبة...",
            options=[
                discord.SelectOption(
                    label=title,
                    value=key
                )
                for key, title in RANK_OPTIONS
            ]
        )

    async def callback(self, interaction):

        key = self.values[0]

        title = next(
            title
            for rank_key, title in RANK_OPTIONS
            if rank_key == key
        )

        await interaction.response.send_modal(
            RoleIDModal(
                interaction.guild.id,
                key,
                title
            )
        )


class RankPanel(discord.ui.View):

    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.add_item(RankSelect(guild_id))


# ==============================
# SHIELDS
# ==============================

class ShieldAmountModal(discord.ui.Modal):

    def __init__(self, guild_id):

        super().__init__(title="تحديد عدد الدروع")

        self.guild_id = guild_id

        self.amount_input = discord.ui.TextInput(
            label="عدد الدروع المتاحة",
            placeholder="مثال: 5",
            required=True,
            max_length=5
        )

        self.add_item(self.amount_input)

    async def on_submit(self, interaction):

        value = self.amount_input.value.strip()

        if not value.isdigit():
            await interaction.response.send_message(
                "❌ أدخل رقم صحيح.",
                ephemeral=True
            )
            return

        set_setting(
            self.guild_id,
            "shield_amount",
            value
        )

        await interaction.response.send_message(
            f"✅ تم تحديد الدروع المتاحة: {value}",
            ephemeral=True
        )


class ShieldPanel(discord.ui.View):

    def __init__(self, guild_id):

        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(
        label="تفعيل الدروع",
        style=discord.ButtonStyle.success,
        emoji="🛡️"
    )
    async def enable_shields(self, interaction, button):

        set_setting(
            self.guild_id,
            "shields_enabled",
            "true"
        )

        await interaction.response.send_message(
            "🟢 تم تفعيل نظام الدروع.",
            ephemeral=True
        )

    @discord.ui.button(
        label="إغلاق الدروع",
        style=discord.ButtonStyle.danger,
        emoji="🔒"
    )
    async def disable_shields(self, interaction, button):

        set_setting(
            self.guild_id,
            "shields_enabled",
            "false"
        )

        await interaction.response.send_message(
            "🔴 تم إغلاق نظام الدروع.",
            ephemeral=True
        )

    @discord.ui.button(
        label="تحديد العدد",
        style=discord.ButtonStyle.primary,
        emoji="🔢"
    )
    async def set_amount(self, interaction, button):

        await interaction.response.send_modal(
            ShieldAmountModal(self.guild_id)
        )
        # ==============================
# CUSTOMIZATION
# ==============================

class CustomEmojiModal(discord.ui.Modal):

    def __init__(self, guild_id, user_id):

        super().__init__(title="تخصيص الإيموجي")

        self.guild_id = guild_id
        self.user_id = user_id

        self.emoji_input = discord.ui.TextInput(
            label="الإيموجي",
            placeholder="أرسل أو الصق الإيموجي",
            required=True,
            max_length=100
        )

        self.add_item(self.emoji_input)

    async def on_submit(self, interaction):

        emoji = self.emoji_input.value.strip()

        with db() as conn:
            conn.execute(
                """
                INSERT INTO customizations
                (guild_id, user_id, emoji)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET emoji=excluded.emoji
                """,
                (
                    self.guild_id,
                    self.user_id,
                    emoji
                )
            )
            conn.commit()

        await interaction.response.send_message(
            f"✅ تم حفظ إيموجيك: {emoji}",
            ephemeral=True
        )


class CustomNickModal(discord.ui.Modal):

    def __init__(self, guild_id, user_id):

        super().__init__(title="تخصيص اللقب")

        self.guild_id = guild_id
        self.user_id = user_id

        self.nick_input = discord.ui.TextInput(
            label="اللقب",
            placeholder="اكتب لقبك",
            required=True,
            max_length=32
        )

        self.add_item(self.nick_input)

    async def on_submit(self, interaction):

        nickname = self.nick_input.value.strip()

        with db() as conn:
            conn.execute(
                """
                INSERT INTO customizations
                (guild_id, user_id, nickname)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET nickname=excluded.nickname
                """,
                (
                    self.guild_id,
                    self.user_id,
                    nickname
                )
            )
            conn.commit()

        await interaction.response.send_message(
            "✅ تم حفظ لقبك.",
            ephemeral=True
        )


class CustomMemberPanel(discord.ui.View):

    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(
        label="اختيار الإيموجي",
        emoji="😀",
        style=discord.ButtonStyle.primary
    )
    async def choose_emoji(self, interaction, button):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "هذه اللوحة ليست لك.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            CustomEmojiModal(
                interaction.guild.id,
                self.user_id
            )
        )

    @discord.ui.button(
        label="اختيار اللقب",
        emoji="🏷️",
        style=discord.ButtonStyle.secondary
    )
    async def choose_nick(self, interaction, button):

        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "هذه اللوحة ليست لك.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            CustomNickModal(
                interaction.guild.id,
                self.user_id
            )
        )


# ==============================
# REACTIONS
# ==============================

class ReactionEmojiModal(discord.ui.Modal):

    def __init__(self, guild_id):

        super().__init__(title="إضافة رياكشن")

        self.guild_id = guild_id

        self.emoji_input = discord.ui.TextInput(
            label="الإيموجي",
            placeholder="أرسل الإيموجي",
            required=True,
            max_length=100
        )

        self.add_item(self.emoji_input)

    async def on_submit(self, interaction):

        emoji = self.emoji_input.value.strip()

        with db() as conn:
            conn.execute(
                """
                INSERT INTO reactions (guild_id, emoji)
                VALUES (?, ?)
                """,
                (self.guild_id, emoji)
            )
            conn.commit()

        await interaction.response.send_message(
            f"✅ تمت إضافة الرياكشن: {emoji}",
            ephemeral=True
        )


class ReactionChannelModal(discord.ui.Modal):

    def __init__(self, guild_id):

        super().__init__(title="تحديد روم الرياكشن")

        self.guild_id = guild_id

        self.channel_input = discord.ui.TextInput(
            label="Channel ID",
            placeholder="أرسل ID الروم",
            required=True,
            max_length=25
        )

        self.add_item(self.channel_input)

    async def on_submit(self, interaction):

        raw = self.channel_input.value.strip()

        if not raw.isdigit():
            await interaction.response.send_message(
                "❌ ID غير صحيح.",
                ephemeral=True
            )
            return

        channel_id = int(raw)

        channel = interaction.guild.get_channel(channel_id)

        if channel is None:
            await interaction.response.send_message(
                "❌ الروم غير موجود.",
                ephemeral=True
            )
            return

        set_setting(
            self.guild_id,
            "reaction_channel",
            channel_id
        )

        await interaction.response.send_message(
            f"✅ روم الرياكشن: {channel.mention}",
            ephemeral=True
        )


class ReactionPanel(discord.ui.View):

    def __init__(self, guild_id):

        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(
        label="إضافة رياكشن",
        emoji="👍",
        style=discord.ButtonStyle.primary
    )
    async def add_reaction(self, interaction, button):

        await interaction.response.send_modal(
            ReactionEmojiModal(self.guild_id)
        )

    @discord.ui.button(
        label="تحديد الروم",
        emoji="📍",
        style=discord.ButtonStyle.secondary
    )
    async def set_channel(self, interaction, button):

        await interaction.response.send_modal(
            ReactionChannelModal(self.guild_id)
        )

    @discord.ui.button(
        label="تشغيل",
        emoji="🟢",
        style=discord.ButtonStyle.success
    )
    async def enable(self, interaction, button):

        set_setting(
            self.guild_id,
            "auto_reactions",
            "true"
        )

        await interaction.response.send_message(
            "🟢 الرياكشن التلقائي مفعّل.",
            ephemeral=True
        )

    @discord.ui.button(
        label="إيقاف",
        emoji="🔴",
        style=discord.ButtonStyle.danger
    )
    async def disable(self, interaction, button):

        set_setting(
            self.guild_id,
            "auto_reactions",
            "false"
        )

        await interaction.response.send_message(
            "🔴 تم إيقاف الرياكشن.",
            ephemeral=True
        )


# ==============================
# DIVIDER
# ==============================

class DividerModal(discord.ui.Modal):

    def __init__(self, guild_id, setting_key, title):

        super().__init__(title=title)

        self.guild_id = guild_id
        self.setting_key = setting_key

        self.input = discord.ui.TextInput(
            label="القيمة",
            placeholder="أرسل القيمة هنا",
            required=True,
            max_length=2000
        )

        self.add_item(self.input)

    async def on_submit(self, interaction):

        set_setting(
            self.guild_id,
            self.setting_key,
            self.input.value.strip()
        )

        await interaction.response.send_message(
            "✅ تم الحفظ.",
            ephemeral=True
        )


class DividerPanel(discord.ui.View):

    def __init__(self, guild_id):

        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(
        label="تحديد روم الفاصل",
        emoji="📍",
        style=discord.ButtonStyle.primary
    )
    async def set_channel(self, interaction, button):

        await interaction.response.send_modal(
            DividerModal(
                self.guild_id,
                "divider_channel",
                "روم الفاصل"
            )
        )

    @discord.ui.button(
        label="رابط الفاصل",
        emoji="🔗",
        style=discord.ButtonStyle.secondary
    )
    async def set_link(self, interaction, button):

        await interaction.response.send_modal(
            DividerModal(
                self.guild_id,
                "divider_url",
                "رابط الفاصل"
            )
        )

    @discord.ui.button(
        label="حذف الفاصل",
        emoji="🗑️",
        style=discord.ButtonStyle.danger
    )
    async def delete_divider(self, interaction, button):

        set_setting(
            self.guild_id,
            "divider_url",
            ""
        )

        await interaction.response.send_message(
            "🗑️ تم حذف رابط الفاصل.",
            ephemeral=True
        )
        # ==============================
# ADMIN SECTION ROUTER
# ==============================

async def open_admin_section(interaction, section):

    guild_id = interaction.guild.id

    if section == "messages":

        await interaction.response.send_message(
            "✉️ **لوحة الرسائل**\n"
            "اختر الرسالة التي تريد تعديلها:",
            view=MessagePanel(guild_id),
            ephemeral=True
        )

    elif section == "announcements":

        view = AnnouncementPanel()

        await interaction.response.send_message(
            "📢 **الإعلانات**\n"
            "اضغط كتابة إعلان:",
            view=view,
            ephemeral=True
        )

    elif section == "ranks":

        await interaction.response.send_message(
            "🏆 **إعداد الرتب**\n"
            "اختر الرتبة وأرسل Role ID:",
            view=RankPanel(guild_id),
            ephemeral=True
        )

    elif section == "shields":

        await interaction.response.send_message(
            "🛡️ **إعداد الدروع**",
            view=ShieldPanel(guild_id),
            ephemeral=True
        )

    elif section == "custom":

        set_setting(
            guild_id,
            "customization_enabled",
            get_setting(
                guild_id,
                "customization_enabled",
                "false"
            )
        )

        await interaction.response.send_message(
            "🎨 **إعداد التخصيص**\n"
            "تقدر تشغيل أو إغلاق تخصيص الأعضاء.",
            view=CustomizationAdminPanel(guild_id),
            ephemeral=True
        )

    elif section == "reactions":

        await interaction.response.send_message(
            "👍 **إعداد الرياكشن التلقائي**",
            view=ReactionPanel(guild_id),
            ephemeral=True
        )

    elif section == "divider":

        await interaction.response.send_message(
            "🖼️ **إعداد الفاصل**",
            view=DividerPanel(guild_id),
            ephemeral=True
        )


# ==============================
# CUSTOMIZATION ADMIN PANEL
# ==============================

class CustomizationAdminPanel(discord.ui.View):

    def __init__(self, guild_id):

        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(
        label="تفعيل التخصيص",
        emoji="🟢",
        style=discord.ButtonStyle.success
    )
    async def enable_custom(self, interaction, button):

        set_setting(
            self.guild_id,
            "customization_enabled",
            "true"
        )

        await interaction.response.send_message(
            "🟢 أصبح التخصيص مسموحًا للأعضاء.",
            ephemeral=True
        )

    @discord.ui.button(
        label="إغلاق التخصيص",
        emoji="🔴",
        style=discord.ButtonStyle.danger
    )
    async def disable_custom(self, interaction, button):

        set_setting(
            self.guild_id,
            "customization_enabled",
            "false"
        )

        await interaction.response.send_message(
            "🔴 تم إغلاق التخصيص للأعضاء.",
            ephemeral=True
        )

    @discord.ui.button(
        label="تغيير أمر التخصيص",
        emoji="⌨️",
        style=discord.ButtonStyle.secondary
    )
    async def change_custom_command(self, interaction, button):

        await interaction.response.send_modal(
            DividerModal(
                self.guild_id,
                "custom_command",
                "أمر التخصيص"
            )
        )


# ==============================
# -STM MESSAGE COMMAND
# ==============================

@bot.command(name="stm")
async def message_panel_command(ctx):

    if not await admin_check(ctx):
        return

    await ctx.send(
        "✉️ **لوحة تعديل الرسائل**\n"
        "اختر الرسالة التي تريد تعديلها:",
        view=MessagePanel(ctx.guild.id)
    )


# ==============================
# -STK MEMBER CUSTOMIZATION
# ==============================

@bot.command(name="stk")
async def member_customization(ctx):

    enabled = get_setting(
        ctx.guild.id,
        "customization_enabled",
        "false"
    )

    if enabled != "true":
        await ctx.reply(
            "🔒 التخصيص مغلق حاليًا."
        )
        return

    await ctx.reply(
        "🎨 **لوحة تخصيص الستريك**\n"
        "اختر الإيموجي أو اللقب:",
        view=CustomMemberPanel(ctx.author.id)
    )
    # ==============================
# STREAK COMMANDS
# ==============================

def get_streak_data(guild_id, user_id):

    with db() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM streaks
            WHERE guild_id=? AND user_id=?
            """,
            (guild_id, user_id)
        ).fetchone()

    return row


@bot.command(name="ستريك")
async def streak_command(ctx):

    data = get_streak_data(
        ctx.guild.id,
        ctx.author.id
    )

    if not data:
        await ctx.reply(
            "🔥 ما عندك ستريك مسجل للحين."
        )
        return

    emoji = ""
    nickname = ""

    with db() as conn:
        custom = conn.execute(
            """
            SELECT emoji, nickname
            FROM customizations
            WHERE guild_id=? AND user_id=?
            """,
            (ctx.guild.id, ctx.author.id)
        ).fetchone()

    if custom:
        emoji = custom["emoji"] or ""
        nickname = custom["nickname"] or ""

    embed = discord.Embed(
        title=f"{emoji} {nickname}".strip(),
        color=discord.Color.orange()
    )

    embed.add_field(
        name="🔥 الستريك الحالي",
        value=str(data["streak"]),
        inline=True
    )

    embed.add_field(
        name="🏆 أطول ستريك",
        value=str(data["best_streak"]),
        inline=True
    )

    embed.add_field(
        name="📸 الصور",
        value=str(data["photos"]),
        inline=True
    )

    await ctx.send(
        content=ctx.author.mention,
        embed=embed
    )


@bot.command(name="ستريكه")
async def member_streak_command(ctx, member: discord.Member):

    data = get_streak_data(
        ctx.guild.id,
        member.id
    )

    if not data:
        await ctx.reply(
            "🔥 العضو ما عنده ستريك مسجل."
        )
        return

    await ctx.reply(
        f"🔥 ستريك {member.mention}: "
        f"**{data['streak']}**\n"
        f"📸 الصور: **{data['photos']}**"
    )


# ==============================
# TOP LEADERBOARDS
# ==============================

async def send_top(ctx, sort_column, title):

    allowed_columns = {
        "streak",
        "photos",
        "best_streak"
    }

    if sort_column not in allowed_columns:
        return

    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT user_id, streak, best_streak, photos
            FROM streaks
            WHERE guild_id=?
            ORDER BY {sort_column} DESC
            LIMIT 10
            """,
            (ctx.guild.id,)
        ).fetchall()

    if not rows:
        await ctx.reply(
            "ما فيه بيانات كافية للتوب للحين."
        )
        return

    lines = []

    for index, row in enumerate(rows, start=1):

        member = ctx.guild.get_member(row["user_id"])

        name = (
            member.display_name
            if member
            else f"عضو {row['user_id']}"
        )

        value = row[sort_column]

        lines.append(
            f"**{index}.** {name} — **{value}**"
        )

    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        color=discord.Color.gold()
    )

    await ctx.send(embed=embed)


@bot.command(name="top")
async def top_command(ctx):
    await send_top(
        ctx,
        "streak",
        "🏆 TOP"
    )


@bot.command(name="tops")
async def tops_command(ctx):
    await send_top(
        ctx,
        "streak",
        "🔥 TOP STREAKS"
    )


@bot.command(name="topc")
async def topc_command(ctx):
    await send_top(
        ctx,
        "photos",
        "📸 TOP PHOTOS"
    )


@bot.command(name="topm")
async def topm_command(ctx):
    await send_top(
        ctx,
        "best_streak",
        "⏳ LONGEST STREAK"
    )
    # ==============================
# PHOTO DETECTION
# ==============================

def has_image(message):

    if message.attachments:

        for attachment in message.attachments:

            if attachment.content_type and (
                attachment.content_type.startswith("image/")
            ):
                return True

            if attachment.filename.lower().endswith(
                (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                return True

    for embed in message.embeds:

        if embed.image and embed.image.url:
            return True

    return False


def now_utc():

    return datetime.now(timezone.utc)


def today_string():

    return now_utc().date().isoformat()


def get_member_streak(guild_id, user_id):

    return get_streak_data(
        guild_id,
        user_id
    )


async def send_dm(member, text):

    if not text:
        return

    try:
        await member.send(text)
    except discord.Forbidden:
        logger.info(
            "DM unavailable for %s",
            member.id
        )
    except Exception as error:
        logger.warning(
            "DM error: %s",
            error
        )


# ==============================
# RECORD PHOTO
# ==============================

async def record_photo(message):

    guild_id = message.guild.id
    user_id = message.author.id
    today = today_string()

    with db() as conn:

        row = conn.execute(
            """
            SELECT *
            FROM streaks
            WHERE guild_id=? AND user_id=?
            """,
            (guild_id, user_id)
        ).fetchone()

        if row is None:

            streak = 1
            best_streak = 1
            photos = 1
            started_at = today

            conn.execute(
                """
                INSERT INTO streaks
                (
                    guild_id,
                    user_id,
                    streak,
                    best_streak,
                    photos,
                    last_photo,
                    started_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    user_id,
                    streak,
                    best_streak,
                    photos,
                    today,
                    started_at
                )
            )

            is_new = True

        else:

            last_photo = row["last_photo"]
            streak = row["streak"]
            best_streak = row["best_streak"]
            photos = row["photos"] + 1
            started_at = row["started_at"]

            if last_photo != today:

                previous_day = (
                    datetime.fromisoformat(last_photo).date()
                )

                current_day = (
                    datetime.fromisoformat(today).date()
                )

                if (current_day - previous_day).days == 1:
                    streak += 1
                else:
                    streak = 1
                    started_at = today

            best_streak = max(
                best_streak,
                streak
            )

            conn.execute(
                """
                UPDATE streaks
                SET streak=?,
                    best_streak=?,
                    photos=?,
                    last_photo=?,
                    started_at=?
                WHERE guild_id=? AND user_id=?
                """,
                (
                    streak,
                    best_streak,
                    photos,
                    today,
                    started_at,
                    guild_id,
                    user_id
                )
            )

            is_new = False

        conn.commit()

    return {
        "is_new": is_new,
        "streak": streak,
        "best_streak": best_streak,
        "photos": photos,
        "started_at": started_at
    }


# ==============================
# PHOTO MESSAGE EVENT
# ==============================

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    if message.guild is None:
        await bot.process_commands(message)
        return

    streak_channel = get_channel_id(
        message.guild.id,
        "streak_channel"
    )

    if (
        streak_channel
        and message.channel.id == streak_channel
        and has_image(message)
    ):

        result = await record_photo(message)

        # رسالة بداية الستريك
        if result["is_new"]:

            start_text = get_setting(
                message.guild.id,
                "start_message",
                "🔥 بدأ ستريكك! أرسل الصور يوميًا للمحافظة عليه."
            )

            await send_dm(
                message.author,
                start_text.format(
                    streak=result["streak"],
                    photos=result["photos"]
                )
            )

        # رسالة عدد الصور
        photo_text = get_setting(
            message.guild.id,
            "photo_message",
            "📸 وصل عدد صورك إلى {photos}"
        )

        await send_dm(
            message.author,
            photo_text.format(
                photos=result["photos"],
                streak=result["streak"]
            )
        )

        # رسالة عدد الستريكات
        count_text = get_setting(
            message.guild.id,
            "streak_count_message"
        )

        if count_text:

            await send_dm(
                message.author,
                count_text.format(
                    streak=result["streak"],
                    photos=result["photos"]
                )
            )

        # سجل الصور
        photo_log_id = get_channel_id(
            message.guild.id,
            "photo_log"
        )

        if photo_log_id:

            log_channel = message.guild.get_channel(
                photo_log_id
            )

            if log_channel:

                await log_channel.send(
                    f"📸 {message.author.mention} "
                    f"— الصور: **{result['photos']}** "
                    f"— الستريك: **{result['streak']}**"
                )

        # الرياكشن التلقائي
        if get_setting(
            message.guild.id,
            "auto_reactions",
            "false"
        ) == "true":

            reaction_channel_id = get_channel_id(
                message.guild.id,
                "reaction_channel"
            )

            if (
                reaction_channel_id
                and message.channel.id == reaction_channel_id
            ):

                with db() as conn:
                    emojis = conn.execute(
                        """
                        SELECT emoji
                        FROM reactions
                        WHERE guild_id=?
                        """,
                        (message.guild.id,)
                    ).fetchall()

                for row in emojis:

                    try:
                        await message.add_reaction(
                            row["emoji"]
                            )
                    except Exception as error:
                        logger.warning(
                            "Reaction failed: %s",
                            error
                        )

        # الفاصل التلقائي
        divider_channel_id = get_channel_id(
            message.guild.id,
            "divider_channel"
        )

        divider_url = get_setting(
            message.guild.id,
            "divider_url"
        )

        if (
            divider_channel_id
            and divider_url
            and message.channel.id == divider_channel_id
        ):

            try:
                await message.channel.send(
                    divider_url
                )
            except Exception as error:
                logger.warning(
                    "Divider failed: %s",
                    error
                )

    await bot.process_commands(message)
    # ==============================
# STREAK REMINDERS
# ==============================

@tasks.loop(minutes=5)
async def streak_reminder_loop():

    current_time = now_utc()

    with db() as conn:

        rows = conn.execute(
            """
            SELECT guild_id, user_id, remind_at
            FROM reminders
            WHERE sent=0
            """
        ).fetchall()

    for row in rows:

        try:

            remind_at = datetime.fromisoformat(
                row["remind_at"]
            )

            if remind_at > current_time:
                continue

            guild = bot.get_guild(
                row["guild_id"]
            )

            if guild is None:
                continue

            member = guild.get_member(
                row["user_id"]
            )

            if member is None:
                continue

            text = get_setting(
                row["guild_id"],
                "reminder_message",
                "⏰ لا تنسى ستريكك! باقي لك 7 ساعات تقريبًا."
            )

            await send_dm(
                member,
                text
            )

            with db() as conn:
                conn.execute(
                    """
                    UPDATE reminders
                    SET sent=1
                    WHERE guild_id=? AND user_id=?
                    """,
                    (
                        row["guild_id"],
                        row["user_id"]
                    )
                )
                conn.commit()

        except Exception as error:

            logger.warning(
                "Reminder error: %s",
                error
            )


@streak_reminder_loop.before_loop
async def before_reminder_loop():

    await bot.wait_until_ready()
    # ==============================
# START BACKGROUND TASKS
# ==============================

@bot.event
async def on_ready():

    init_db()

    if not streak_reminder_loop.is_running():
        streak_reminder_loop.start()

    logger.info(
        "ST connected as %s (%s)",
        bot.user,
        bot.user.id
    )

    print(f"ST is ready: {bot.user}")


# ==============================
# ERROR HANDLING
# ==============================

@bot.event
async def on_command_error(ctx, error):

    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply(
            "❌ ناقصك تحديد العضو أو البيانات المطلوبة."
        )
        return

    if isinstance(error, commands.BadArgument):
        await ctx.reply(
            "❌ البيانات اللي أرسلتها غير صحيحة."
        )
        return

    logger.error(
        "Command error",
        exc_info=error
    )


# ==============================
# RUN BOT
# ==============================

if not TOKEN:

    raise RuntimeError(
        "DISCORD_TOKEN is missing in Railway Variables."
    )

bot.run(TOKEN)
