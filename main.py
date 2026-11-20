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

REACTION_DEFAULT='ð¥,â¤ï¸,ð¯'
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

@bot.command(name='ÙÙØ­Ø©')
@commands.has_guild_permissions(manage_guild=True)
async def panel(ctx):
 await ctx.send('**ÙÙØ­Ø© ØªØ­ÙÙ Ø§ÙØ³ØªØ±ÙÙ**\nØ§Ø®ØªØ± Ø§ÙØ²Ø± ÙØªØ¹Ø¯ÙÙ Ø§ÙØ¥Ø¹Ø¯Ø§Ø¯Ø§Øª. ÙÙ Ø§ÙØ£Ø²Ø±Ø§Ø± ÙÙØ§ ÙÙØ£Ø¯ÙÙ ÙÙØ·.',view=AdminPanel())

class AdminPanel(discord.ui.View):
 def __init__(self): super().__init__(timeout=600)
 @discord.ui.button(label='ØªØ­Ø¯ÙØ¯ ÙÙØ§Ø© Ø§ÙØ³ØªØ±ÙÙ',style=discord.ButtonStyle.primary)
 async def channel(self,interaction,button): await interaction.response.send_modal(ChannelModal())
 @discord.ui.button(label='Ø§ÙØ¥ÙÙÙØ¬ÙØ§Øª',style=discord.ButtonStyle.primary)
 async def emoji(self,interaction,button): await interaction.response.send_modal(EmojiModal())
 @discord.ui.button(label='Ø±Ø³Ø§Ø¦Ù Ø§ÙØ®Ø§Øµ',style=discord.ButtonStyle.primary)
 async def messages(self,interaction,button): await interaction.response.send_modal(MessagesModal())
 @discord.ui.button(label='ØµÙØ±Ø© Ø§ÙÙØ§ØµÙ',style=discord.ButtonStyle.primary)
 async def divider(self,interaction,button): await interaction.response.send_modal(DividerModal())
 @discord.ui.button(label='Ø§ÙØªØ°ÙÙØ±',style=discord.ButtonStyle.primary)
 async def reminder_settings(self,interaction,button): await interaction.response.send_modal(ReminderModal())
 @discord.ui.button(label='ØªÙØ¹ÙÙ/ØªØ¹Ø·ÙÙ Ø§ÙÙÙØ²Ø§Øª',style=discord.ButtonStyle.secondary)
 async def toggles(self,interaction,button): await interaction.response.send_modal(ToggleModal())
 @discord.ui.button(label='Ø¥Ø¹ÙØ§Ù ÙÙØ£Ø¹Ø¶Ø§Ø¡',style=discord.ButtonStyle.danger)
 async def broadcast(self,interaction,button): await interaction.response.send_modal(BroadcastModal())
 @discord.ui.button(label='Ø§Ø®ØªØ¨Ø§Ø± Ø§ÙØ¨ÙØª',style=discord.ButtonStyle.success)
 async def test(self,interaction,button): await interaction.response.send_message(f'Ø§ÙØ¨ÙØª ÙØ¹ÙÙ â {bot.user}',ephemeral=True)
 async def interaction_check(self,interaction):
  if not interaction.user.guild_permissions.manage_guild:
   await interaction.response.send_message('ÙØ°Ù Ø§ÙÙÙØ­Ø© ÙÙØ£Ø¯ÙÙ ÙÙØ·.',ephemeral=True); return False
  return True

class ChannelModal(discord.ui.Modal,title='ØªØ­Ø¯ÙØ¯ ÙÙØ§Ø© Ø§ÙØ³ØªØ±ÙÙ'):
 channel_id=discord.ui.TextInput(label='ID ÙÙØ§Ø© Ø§ÙØ³ØªØ±ÙÙ',placeholder='Ø­Ø· Ø±ÙÙ Ø§ÙÙÙØ§Ø© ÙÙØ·',required=True,max_length=25)
 async def on_submit(self,interaction):
  setcfg('channel_id',self.channel_id.value.strip()); await interaction.response.send_message('ØªÙ Ø­ÙØ¸ ÙÙØ§Ø© Ø§ÙØ³ØªØ±ÙÙ â',ephemeral=True)
class EmojiModal(discord.ui.Modal,title='ØªØ¹Ø¯ÙÙ Ø§ÙØ¥ÙÙÙØ¬ÙØ§Øª'):
 values=discord.ui.TextInput(label='Ø§ÙØ¥ÙÙÙØ¬ÙØ§Øª ÙÙØµÙÙØ© Ø¨ÙØ§ØµÙØ©',placeholder='ð¥, â¤ï¸, ð¯ Ø£Ù Ø¥ÙÙÙØ¬Ù Ø§ÙØ³ÙØ±ÙØ±',required=True,max_length=300)
 async def on_submit(self,interaction):
  setcfg('reactions',self.values.value); await interaction.response.send_message('ØªÙ Ø­ÙØ¸ Ø§ÙØ¥ÙÙÙØ¬ÙØ§Øª â',ephemeral=True)
class MessagesModal(discord.ui.Modal,title='ØªØ¹Ø¯ÙÙ Ø±Ø³Ø§Ø¦Ù Ø§ÙØ®Ø§Øµ'):
 start=discord.ui.TextInput(label='Ø±Ø³Ø§ÙØ© Ø¨Ø¯Ø§ÙØ© Ø§ÙØ³ØªØ±ÙÙ',default=cfg('dm_start','Ø¨Ø¯ÙØª Ø³ØªØ±ÙÙ ÙØ§ ÙØ­Ø´ Ø§Ø³ØªÙØ± ð¥'),max_length=500)
 photo=discord.ui.TextInput(label='Ø±Ø³Ø§ÙØ© Ø¨Ø¹Ø¯ ÙÙ ØµÙØ±Ø©',default=cfg('dm_photo','ØªÙ ØªØ³Ø¬ÙÙ ØµÙØ±ØªÙ! Ø³ØªØ±ÙÙÙ Ø§ÙØ­Ø§ÙÙ: {streak} ÙÙÙ ð¥'),max_length=500)
 reminder_text=discord.ui.TextInput(label='Ø±Ø³Ø§ÙØ© Ø§ÙØªØ°ÙÙØ±',default=cfg('dm_reminder','â° ÙØ§ ØªÙØ³Ù Ø§ÙØ³ØªØ±ÙÙ Ø§ÙÙÙÙ! Ø¨Ø§ÙÙ ÙÙØª Ø¹ÙÙ ÙÙØ§ÙØ© Ø§ÙÙÙÙ ð¥'),max_length=500)
 async def on_submit(self,interaction):
  setcfg('dm_start',self.start.value); setcfg('dm_photo',self.photo.value); setcfg('dm_reminder',self.reminder_text.value)
  await interaction.response.send_message('ØªÙ Ø­ÙØ¸ Ø±Ø³Ø§Ø¦Ù Ø§ÙØ®Ø§Øµ â',ephemeral=True)
class DividerModal(discord.ui.Modal,title='ØµÙØ±Ø© Ø§ÙÙØ§ØµÙ'):
 url=discord.ui.TextInput(label='Ø±Ø§Ø¨Ø· Ø§ÙØµÙØ±Ø© (URL)',placeholder='https://...',required=False,max_length=1000)
 async def on_submit(self,interaction): setcfg('divider_url',self.url.value.strip()); await interaction.response.send_message('ØªÙ Ø­ÙØ¸ Ø±Ø§Ø¨Ø· Ø§ÙÙØ§ØµÙ. Ø§ØªØ±ÙÙ ÙØ§Ø±ØºÙØ§ ÙØ¥ÙØºØ§Ø¡ Ø§ÙØµÙØ±Ø©.',ephemeral=True)
class ReminderModal(discord.ui.Modal,title='Ø¥Ø¹Ø¯Ø§Ø¯ Ø§ÙØªØ°ÙÙØ±'):
 hour=discord.ui.TextInput(label='Ø³Ø§Ø¹Ø© Ø§ÙØªØ°ÙÙØ± Ø¨ØªÙÙÙØª Ø§ÙÙÙÙØª (0-23)',default=cfg('reminder_hour','19'),max_length=2)
 async def on_submit(self,interaction):
  try:
   h=int(self.hour.value)
   if not 0<=h<=23: raise ValueError
   setcfg('reminder_hour',h); await interaction.response.send_message(f'ØªÙ Ø¶Ø¨Ø· Ø§ÙØªØ°ÙÙØ± Ø¹ÙÙ Ø§ÙØ³Ø§Ø¹Ø© {h:02}:00 Ø¨ØªÙÙÙØª Ø§ÙÙÙÙØª â',ephemeral=True)
  except ValueError: await interaction.response.send_message('Ø£Ø¯Ø®Ù Ø³Ø§Ø¹Ø© ØµØ­ÙØ­Ø© ÙÙ 0 Ø¥ÙÙ 23.',ephemeral=True)
class ToggleModal(discord.ui.Modal,title='ØªÙØ¹ÙÙ/ØªØ¹Ø·ÙÙ'):
 dm=discord.ui.TextInput(label='Ø§ÙØ®Ø§Øµ Ø¨Ø¹Ø¯ Ø§ÙØµÙØ± (on/off)',default=cfg('feature_dm','on'),max_length=3)
 reminder=discord.ui.TextInput(label='Ø§ÙØªØ°ÙÙØ±Ø§Øª (on/off)',default=cfg('feature_reminder','on'),max_length=3)
 reactions=discord.ui.TextInput(label='Ø§ÙØªÙØ§Ø¹ÙØ§Øª (on/off)',default=cfg('feature_reactions','on'),max_length=3)
 async def on_submit(self,interaction):
  for k,v in [('feature_dm',self.dm.value),('feature_reminder',self.reminder.value),('feature_reactions',self.reactions.value)]: setcfg(k,'1' if v.lower() in ('on','1','yes') else '0')
  await interaction.response.send_message('ØªÙ ØªØ­Ø¯ÙØ« ØªØ´ØºÙÙ Ø§ÙÙÙØ²Ø§Øª. Ø§Ø³ØªØ®Ø¯Ù on Ø£Ù off.',ephemeral=True)
class BroadcastModal(discord.ui.Modal,title='Ø¥Ø±Ø³Ø§Ù Ø¥Ø¹ÙØ§Ù'):
 message=discord.ui.TextInput(label='ÙØµ Ø§ÙØ¥Ø¹ÙØ§Ù (ÙØ±Ø³Ù ÙØ£Ø¹Ø¶Ø§Ø¡ Ø§ÙØ³ØªØ±ÙÙ)',style=discord.TextStyle.paragraph,max_length=1800)
 async def on_submit(self,interaction):
  await interaction.response.send_message('Ø¨Ø¯Ø£ Ø¥Ø±Ø³Ø§Ù Ø§ÙØ¥Ø¹ÙØ§Ù Ø¥ÙÙ Ø§ÙÙØ´Ø§Ø±ÙÙÙ Ø¨Ø§ÙØ³ØªØ±ÙÙâ¦',ephemeral=True)
  count=0
  for row in DB.execute('SELECT user_id FROM users').fetchall():
   try: await (await bot.fetch_user(row['user_id'])).send(self.message.value); count+=1
   except discord.HTTPException: pass
  try: await interaction.followup.send(f'ØªÙ Ø¥Ø±Ø³Ø§Ù Ø§ÙØ¥Ø¹ÙØ§Ù Ø¥ÙÙ {count} Ø¹Ø¶Ù.',ephemeral=True)
  except discord.HTTPException: pass

@bot.command(name='Ø³ØªØ±ÙÙÙ')
async def streak(ctx):
 r=userrow(ctx.author.id)
 if not r: return await ctx.reply('ÙØ§ Ø¹ÙØ¯Ù Ø³ØªØ±ÙÙ ÙØ³Ø¬Ù ÙÙØ­ÙÙ ð¸')
 current=active(r); photos=r['photos'] if current else 0
 embed=discord.Embed(title=f'ð Ø³ØªØ±ÙÙ {ctx.author.display_name}',color=discord.Color.orange())
 embed.set_thumbnail(url=ctx.author.display_avatar.url)
 embed.add_field(name='ð¥ Ø§ÙØ­Ø§ÙÙ',value=f'{current} ÙÙÙ',inline=True)
 embed.add_field(name='ð Ø§ÙØ£Ø·ÙÙ',value=f'{r["longest_streak"]} ÙÙÙ',inline=True)
 embed.add_field(name='ð¸ ØµÙØ± Ø§ÙØ­Ø§ÙÙ',value=str(photos),inline=True)
 await ctx.reply(embed=embed)

async def leaderboard(ctx,period='all',metric='streak'):
 if period=='all': rows=DB.execute('SELECT user_id,longest_streak AS val FROM users ORDER BY val DESC LIMIT 10').fetchall(); title='ð Ø£Ø·ÙÙ Ø³ØªØ±ÙÙØ§Øª'
 else:
  days=7 if period=='week' else 30; since=(today()-timedelta(days=days-1)).isoformat()
  rows=DB.execute('SELECT user_id,SUM(photos) AS val FROM activity WHERE day>=? GROUP BY user_id ORDER BY val DESC LIMIT 10',(since,)).fetchall(); title='ð¸ Ø£ÙØ«Ø± ØµÙØ± Ø¢Ø®Ø± '+('7 Ø£ÙØ§Ù' if period=='week' else '30 ÙÙÙ')
 if not rows: return await ctx.reply('ÙØ§ ÙÙÙ Ø¨ÙØ§ÙØ§Øª ÙØ§ÙÙØ© ÙÙØ­ÙÙ.')
 lines=[]
 for i,r in enumerate(rows,1):
  try: m=await ctx.guild.fetch_member(r['user_id']); name=m.display_name
  except: name=f'Ø¹Ø¶Ù {r["user_id"]}'
  unit='ÙÙÙ' if period=='all' else 'ØµÙØ±Ø©'
  lines.append(f'**{i}.** {name} â **{r["val"]} {unit}**')
 await ctx.reply(title+'\n'+'\n'.join(lines))
@bot.command(name='ØªÙØ¨_Ø³ØªØ±ÙÙ')
async def top_streak(ctx): await leaderboard(ctx,'all')
@bot.command(name='tops')
async def tops(ctx): await leaderboard(ctx,'all')
@bot.command(name='ØªÙØ¨_ØµÙØ±')
async def top_photos(ctx): await leaderboard(ctx,'month')
@bot.command(name='ØªÙØ¨_Ø£Ø³Ø¨ÙØ¹')
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
     except: await message.channel.send('ââââââââ ð¥ Ø¨Ø¯Ø§ÙØ© Ø³ØªØ±ÙÙ Ø¬Ø¯ÙØ¯ ð¥ ââââââââ')
    else: await message.channel.send('ââââââââ ð¥ Ø¨Ø¯Ø§ÙØ© Ø³ØªØ±ÙÙ Ø¬Ø¯ÙØ¯ ð¥ ââââââââ')
    if enabled('feature_dm'):
     await dm_text(message.author,cfg('dm_start','Ø¨Ø¯ÙØª Ø³ØªØ±ÙÙ ÙØ§ ÙØ­Ø´ Ø§Ø³ØªÙØ± ð¥'))
   if enabled('feature_reactions'):
    for e in reactions()[:3]:
     try: await message.add_reaction(e)
     except discord.HTTPException: pass
   if enabled('feature_dm'):
    msg=cfg('dm_photo','ØªÙ ØªØ³Ø¬ÙÙ ØµÙØ±ØªÙ! Ø³ØªØ±ÙÙÙ Ø§ÙØ­Ø§ÙÙ: {streak} ÙÙÙ ð¥').replace('{streak}',str(streak)).replace('{photos}',str(total))
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
   u=await bot.fetch_user(r['user_id']); await dm_text(u,cfg('dm_reminder','â° ÙØ§ ØªÙØ³Ù Ø§ÙØ³ØªØ±ÙÙ Ø§ÙÙÙÙ! Ø¨Ø§ÙÙ ÙÙØª Ø¹ÙÙ ÙÙØ§ÙØ© Ø§ÙÙÙÙ ð¥'))
  except discord.HTTPException: pass
  DB.execute('UPDATE users SET reminder_day=? WHERE user_id=?',(day,r['user_id']))
 DB.commit()
@reminder.before_loop
async def wait_ready(): await bot.wait_until_ready()

if not TOKEN: raise RuntimeError('Missing DISCORD_TOKEN environment variable')
bot.run(TOKEN)
