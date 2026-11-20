import os, sqlite3
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks

TZ = ZoneInfo('Asia/Kuwait')
TOKEN = os.getenv('DISCORD_TOKEN')
DB = sqlite3.connect('streaks.db', check_same_thread=False)
DB.row_factory = sqlite3.Row

DB.executescript('''
CREATE TABLE IF NOT EXISTS users(
 user_id INTEGER PRIMARY KEY, current_streak INTEGER DEFAULT 0,
 longest_streak INTEGER DEFAULT 0, photos INTEGER DEFAULT 0,
 last_day TEXT, reminder_day TEXT);
CREATE TABLE IF NOT EXISTS activity(
 user_id INTEGER NOT NULL, day TEXT NOT NULL, photos INTEGER DEFAULT 0,
 PRIMARY KEY(user_id, day));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
''')
DB.commit()

def cfg(key, default=''):
 r=DB.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
 return r['value'] if r else default

def setcfg(key,value):
 DB.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))
 DB.commit()

def enabled(key, default='1'): return cfg(key,default) == '1'
def today(): return datetime.now(TZ).date()

def userrow(uid): return DB.execute('SELECT * FROM users WHERE user_id=?',(uid,)).fetchone()
def active(row):
 if not row or not row['last_day']: return 0
 return row['current_streak'] if date.fromisoformat(row['last_day']) >= today()-timedelta(days=1) else 0

def add_photos(uid,n):
 d=today(); ds=d.isoformat(); r=userrow(uid); new=False
 if not r:
  streak=1; longest=1; total=n; new=True
  DB.execute('INSERT INTO users(user_id,current_streak,longest_streak,photos,last_day) VALUES(?,?,?,?,?)',(uid,streak,longest,total,ds))
 else:
  last=date.fromisoformat(r['last_day']) if r['last_day'] else None
  if last==d: streak=r['current_streak']; total=r['photos']+n
  elif last==d-timedelta(days=1): streak=r['current_streak']+1; total=r['photos']+n
  else: streak=1; total=n; new=True
  longest=max(r['longest_streak'],streak)
  DB.execute('UPDATE users SET current_streak=?,longest_streak=?,photos=?,last_day=? WHERE user_id=?',(streak,longest,total,ds,uid))
 DB.execute('INSERT INTO activity(user_id,day,photos) VALUES(?,?,?) ON CONFLICT(user_id,day) DO UPDATE SET photos=photos+excluded.photos',(uid,ds,n))
 DB.commit(); return streak,longest,total,new

intents=discord.Intents.default(); intents.message_content=True; intents.members=True
bot=commands.Bot(command_prefix='-',intents=intents)

REACTION_DEFAULT='🔥,❤️,💯'
def reactions(): return [x.strip() for x in cfg('reactions',REACTION_DEFAULT).split(',') if x.strip()]
def is_image(att): return (att.content_type or '').startswith('image/') or att.filename.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp','.bmp'))
def admin():
 return lambda ctx: ctx.author.guild_permissions.manage_guild

async def dm_text(member,text):
 try: await member.send(text.replace('{user}',member.display_name).replace('{mention}',member.mention))
 except discord.HTTPException: pass

@bot.event
async def on_ready():
 print('Bot online:',bot.user)
 reminder.start() if not reminder.is_running() else None

@bot.command(name='لوحة')
@commands.has_guild_permissions(manage_guild=True)
async def panel(ctx):
 await ctx.send('**لوحة تحكم الستريك**\nاختر الزر لتعديل الإعدادات. كل الأزرار هنا للأدمن فقط.',view=AdminPanel())

class AdminPanel(discord.ui.View):
 def __init__(self): super().__init__(timeout=600)
 @discord.ui.button(label='تحديد قناة الستريك',style=discord.ButtonStyle.primary)
 async def channel(self,interaction,button): await interaction.response.send_modal(ChannelModal())
 @discord.ui.button(label='الإيموجيات',style=discord.ButtonStyle.primary)
 async def emoji(self,interaction,button): await interaction.response.send_modal(EmojiModal())
 @discord.ui.button(label='رسائل الخاص',style=discord.ButtonStyle.primary)
 async def messages(self,interaction,button): await interaction.response.send_modal(MessagesModal())
 @discord.ui.button(label='صورة الفاصل',style=discord.ButtonStyle.primary)
 async def divider(self,interaction,button): await interaction.response.send_modal(DividerModal())
 @discord.ui.button(label='التذكير',style=discord.ButtonStyle.primary)
 async def reminder_settings(self,interaction,button): await interaction.response.send_modal(ReminderModal())
 @discord.ui.button(label='تفعيل/تعطيل الميزات',style=discord.ButtonStyle.secondary)
 async def toggles(self,interaction,button): await interaction.response.send_modal(ToggleModal())
 @discord.ui.button(label='إعلان للأعضاء',style=discord.ButtonStyle.danger)
 async def broadcast(self,interaction,button): await interaction.response.send_modal(BroadcastModal())
 @discord.ui.button(label='اختبار البوت',style=discord.ButtonStyle.success)
 async def test(self,interaction,button): await interaction.response.send_message(f'البوت يعمل ✅ {bot.user}',ephemeral=True)
 async def interaction_check(self,interaction):
  if not interaction.user.guild_permissions.manage_guild:
   await interaction.response.send_message('هذه اللوحة للأدمن فقط.',ephemeral=True); return False
  return True

class ChannelModal(discord.ui.Modal,title='تحديد قناة الستريك'):
 channel_id=discord.ui.TextInput(label='ID قناة الستريك',placeholder='حط رقم القناة فقط',required=True,max_length=25)
 async def on_submit(self,interaction):
  setcfg('channel_id',self.channel_id.value.strip()); await interaction.response.send_message('تم حفظ قناة الستريك ✅',ephemeral=True)
class EmojiModal(discord.ui.Modal,title='تعديل الإيموجيات'):
 values=discord.ui.TextInput(label='الإيموجيات مفصولة بفاصلة',placeholder='🔥, ❤️, 💯 أو إيموجي السيرفر',required=True,max_length=300)
 async def on_submit(self,interaction):
  setcfg('reactions',self.values.value); await interaction.response.send_message('تم حفظ الإيموجيات ✅',ephemeral=True)
class MessagesModal(discord.ui.Modal,title='تعديل رسائل الخاص'):
 start=discord.ui.TextInput(label='رسالة بداية الستريك',default=cfg('dm_start','بديت ستريك يا وحش استمر 🔥'),max_length=500)
 photo=discord.ui.TextInput(label='رسالة بعد كل صورة',default=cfg('dm_photo','تم تسجيل صورتك! ستريكك الحالي: {streak} يوم 🔥'),max_length=500)
 reminder_text=discord.ui.TextInput(label='رسالة التذكير',default=cfg('dm_reminder','⏰ لا تنسى الستريك اليوم! باقي وقت على نهاية اليوم 🔥'),max_length=500)
 async def on_submit(self,interaction):
  setcfg('dm_start',self.start.value); setcfg('dm_photo',self.photo.value); setcfg('dm_reminder',self.reminder_text.value)
  await interaction.response.send_message('تم حفظ رسائل الخاص ✅',ephemeral=True)
class DividerModal(discord.ui.Modal,title='صورة الفاصل'):
 url=discord.ui.TextInput(label='رابط الصورة (URL)',placeholder='https://...',required=False,max_length=1000)
 async def on_submit(self,interaction): setcfg('divider_url',self.url.value.strip()); await interaction.response.send_message('تم حفظ رابط الفاصل. اتركه فارغًا لإلغاء الصورة.',ephemeral=True)
class ReminderModal(discord.ui.Modal,title='إعداد التذكير'):
 hour=discord.ui.TextInput(label='ساعة التذكير بتوقيت الكويت (0-23)',default=cfg('reminder_hour','19'),max_length=2)
 async def on_submit(self,interaction):
  try:
   h=int(self.hour.value)
   if not 0<=h<=23: raise ValueError
   setcfg('reminder_hour',h); await interaction.response.send_message(f'تم ضبط التذكير على الساعة {h:02}:00 بتوقيت الكويت ✅',ephemeral=True)
  except ValueError: await interaction.response.send_message('أدخل ساعة صحيحة من 0 إلى 23.',ephemeral=True)
class ToggleModal(discord.ui.Modal,title='تفعيل/تعطيل'):
 dm=discord.ui.TextInput(label='الخاص بعد الصور (on/off)',default=cfg('feature_dm','on'),max_length=3)
 reminder=discord.ui.TextInput(label='التذكيرات (on/off)',default=cfg('feature_reminder','on'),max_length=3)
 reactions=discord.ui.TextInput(label='التفاعلات (on/off)',default=cfg('feature_reactions','on'),max_length=3)
 async def on_submit(self,interaction):
  for k,v in [('feature_dm',self.dm.value),('feature_reminder',self.reminder.value),('feature_reactions',self.reactions.value)]: setcfg(k,'1' if v.lower() in ('on','1','yes') else '0')
  await interaction.response.send_message('تم تحديث تشغيل الميزات. استخدم on أو off.',ephemeral=True)
class BroadcastModal(discord.ui.Modal,title='إرسال إعلان'):
 message=discord.ui.TextInput(label='نص الإعلان (يرسل لأعضاء الستريك)',style=discord.TextStyle.paragraph,max_length=1800)
 async def on_submit(self,interaction):
  await interaction.response.send_message('بدأ إرسال الإعلان إلى المشاركين بالستريك…',ephemeral=True)
  count=0
  for row in DB.execute('SELECT user_id FROM users').fetchall():
   try: await (await bot.fetch_user(row['user_id'])).send(self.message.value); count+=1
   except discord.HTTPException: pass
  try: await interaction.followup.send(f'تم إرسال الإعلان إلى {count} عضو.',ephemeral=True)
  except discord.HTTPException: pass

@bot.command(name='ستريكي')
async def streak(ctx):
 r=userrow(ctx.author.id)
 if not r: return await ctx.reply('ما عندك ستريك مسجل للحين 📸')
 current=active(r); photos=r['photos'] if current else 0
 embed=discord.Embed(title=f'📊 ستريك {ctx.author.display_name}',color=discord.Color.orange())
 embed.set_thumbnail(url=ctx.author.display_avatar.url)
 embed.add_field(name='🔥 الحالي',value=f'{current} يوم',inline=True)
 embed.add_field(name='🏆 الأطول',value=f'{r["longest_streak"]} يوم',inline=True)
 embed.add_field(name='📸 صور الحالي',value=str(photos),inline=True)
 await ctx.reply(embed=embed)

async def leaderboard(ctx,period='all',metric='streak'):
 if period=='all': rows=DB.execute('SELECT user_id,longest_streak AS val FROM users ORDER BY val DESC LIMIT 10').fetchall(); title='🏆 أطول ستريكات'
 else:
  days=7 if period=='week' else 30; since=(today()-timedelta(days=days-1)).isoformat()
  rows=DB.execute('SELECT user_id,SUM(photos) AS val FROM activity WHERE day>=? GROUP BY user_id ORDER BY val DESC LIMIT 10',(since,)).fetchall(); title='📸 أكثر صور آخر '+('7 أيام' if period=='week' else '30 يوم')
 if not rows: return await ctx.reply('ما فيه بيانات كافية للحين.')
 lines=[]
  for i,r in enumerate(rows,1):
        try: m=await ctx.guild.fetch_member(r['user_id']); name=m.display_name
  except: name=f'عضو {r["user_id"]}'
  unit='يوم' if period=='all' else 'صورة'
  lines.append(f'**{i}.** {name} — **{r["val"]} {unit}**')
 await ctx.reply(title+'\n'+'\n'.join(lines))
@bot.command(name='توب_ستريك')
async def top_streak(ctx): await leaderboard(ctx,'all')
@bot.command(name='tops')
async def tops(ctx): await leaderboard(ctx,'all')
@bot.command(name='توب_صور')
async def top_photos(ctx): await leaderboard(ctx,'month')
@bot.command(name='توب_أسبوع')
async def top_week(ctx): await leaderboard(ctx,'week')

@bot.event
async def on_message(message):
 if message.author.bot: return
 if message.guild and message.channel.id==int(cfg('channel_id','0') or 0):
  pics=[a for a in message.attachments if is_image(a)]
  if pics:
   streak,longest,total,new=add_photos(message.author.id,len(pics))
   if new:
    url=cfg('divider_url','')
    if url:
     try: await message.channel.send(embed=discord.Embed().set_image(url=url))
     except: await message.channel.send('━━━━━━━━ 🔥 بداية ستريك جديد 🔥 ━━━━━━━━')
    else: await message.channel.send('━━━━━━━━ 🔥 بداية ستريك جديد 🔥 ━━━━━━━━')
    if enabled('feature_dm'):
     await dm_text(message.author,cfg('dm_start','بديت ستريك يا وحش استمر 🔥'))
   if enabled('feature_reactions'):
    for e in reactions()[:3]:
     try: await message.add_reaction(e)
     except discord.HTTPException: pass
   if enabled('feature_dm'):
    msg=cfg('dm_photo','تم تسجيل صورتك! ستريكك الحالي: {streak} يوم 🔥').replace('{streak}',str(streak)).replace('{photos}',str(total))
    await dm_text(message.author,msg)
 await bot.process_commands(message)

@tasks.loop(minutes=1)
async def reminder():
 if not enabled('feature_reminder'): return
 now=datetime.now(TZ); hour=int(cfg('reminder_hour','19'))
 if now.hour<hour: return
 day=now.date().isoformat(); yesterday=(now.date()-timedelta(days=1)).isoformat()
 rows=DB.execute('SELECT * FROM users WHERE last_day=?',(yesterday,)).fetchall()
 for r in rows:
  if r['reminder_day']==day: continue
  try:
   u=await bot.fetch_user(r['user_id']); await dm_text(u,cfg('dm_reminder','⏰ لا تنسى الستريك اليوم! باقي وقت على نهاية اليوم 🔥'))
  except discord.HTTPException: pass
  DB.execute('UPDATE users SET reminder_day=? WHERE user_id=?',(day,r['user_id']))
 DB.commit()
@reminder.before_loop
async def wait_ready(): await bot.wait_until_ready()

if not TOKEN: raise RuntimeError('Missing DISCORD_TOKEN environment variable')
bot.run(TOKEN) 
