import asyncio
import io
import os
from collections import deque
from typing import List
import random
import requests
import discord
from discord.ext import commands, tasks
from discord import app_commands
from loguru import logger
from dotenv import load_dotenv

load_dotenv()

MY_GUILD = discord.Object(id=os.getenv("MY_GUILD"))
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# ===== إضافة الصوت الجديد هنا =====
DEFAULT_VOICE_ID = "IES4nrmZdUBHByLBde0P"  # استبدل VOICE_ID_HERE بالصوت الجديد
# ===== مثال: DEFAULT_VOICE_ID = "TxGEqnHWrfWFTfGW9XjX" =====

class VoiceManager:
    """Manages voice-related operations."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.voice_cache = []
        self.user_voices = {}
        self.session = requests.Session()

    def fetch_voices(self):
        url = "https://api.elevenlabs.io/v1/voices"
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        self.voice_cache = response.json().get('voices', [])

    def find_voice_by_name(self, name: str):
        return next((voice for voice in self.voice_cache if voice["name"] == name), None)

    def fetch_audio_stream(self, text: str, voice_id: str):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        params = {
            "optimize_streaming_latency": 1
        }
        payload = {
            "model_id": "eleven_multilingual_v2",
            "text": text,
            "voice_settings": {
                "stability": 0.7,
                "similarity_boost": 0.8,
                "style": 0.5,
                "use_speaker_boost": True
            }
        }
        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json"
        }

        try:
            response = self.session.post(url, params=params, headers=headers, json=payload, stream=True)
            response.raise_for_status()
            return io.BytesIO(response.content)
        except requests.RequestException as e:
            logger.error(f"Error fetching audio stream: {e}")
            return None


class MyBot(commands.Bot):
    def __init__(self, intents: discord.Intents, voice_manager: VoiceManager):
        super().__init__(command_prefix='!', intents=intents)
        self.voice_manager = voice_manager
        self.audio_queue = deque()

    async def setup_hook(self):
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)
        self.update_voice_cache.start()

    @tasks.loop(minutes=2)
    async def update_voice_cache(self):
        try:
            self.voice_manager.fetch_voices()
        except requests.RequestException as e:
            logger.error(f"Error updating voice cache: {e}")

    def play_next_audio(self, guild: discord.Guild, error=None):
        if error:
            logger.error(f'Player error: {error}')
        if self.audio_queue:
            audio_stream = self.audio_queue.popleft()
            audio_stream.seek(0)  # Ensure the stream is at the start
            source = discord.FFmpegPCMAudio(audio_stream, pipe=True)
            guild.voice_client.play(source, after=lambda e: self.play_next_audio(guild, e))
        else:
            logger.info("No more audio in the queue.")


intents = discord.Intents.all()
intents.message_content = True  # تمكين قراءة محتوى الرسائل
voice_manager = VoiceManager(api_key=ELEVENLABS_API_KEY)
bot = MyBot(intents=intents, voice_manager=voice_manager)


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')


# ===== الحدث الجديد للتعامل مع الرسائل التي تبدأ ب "-" =====
@bot.event
async def on_message(message: discord.Message):
    # تجاهل رسائل البوت نفسه
    if message.author == bot.user:
        return

    # التعامل مع الرسائل التي تبدأ ب "-"
    if message.content.startswith('-'):
        text = message.content[1:].strip()  # إزالة البادئة والمسافات الزائدة

        # التحقق من طول النص
        if len(text) > 100:
            await message.channel.send(f'الحد الأقصى 100 حرف. النص طويل جدًا ({len(text)} حرفًا).')
            return

        # التأكد من اتصال البوت بقناة صوتية
        if not message.author.voice:
            await message.channel.send('يجب أن تكون في قناة صوتية أولاً!')
            return

        voice_client = message.guild.voice_client
        channel = message.author.voice.channel

        # الاتصال أو الانتقال إلى القناة
        if not voice_client or not voice_client.is_connected():
            voice_client = await channel.connect()
        elif voice_client.channel != channel:
            await voice_client.move_to(channel)

        # الحصول على الصوت المناسب
        global_name = str(message.author.global_name)
        user_voice = bot.voice_manager.user_voices.get(global_name)
        voice_id = DEFAULT_VOICE_ID if user_voice is None else user_voice['voice_id']

        # توليد الصوت
        audio_stream = bot.voice_manager.fetch_audio_stream(text, voice_id)
        if not audio_stream:
            await message.channel.send("فشل توليد الصوت.")
            return

        # إضافة الصوت إلى الطابور
        bot.audio_queue.append(audio_stream)
        if not voice_client.is_playing():
            bot.play_next_audio(message.guild)
        
        # إضافة علامة تفاعل للإشارة أن الرسالة تم معالجتها
        await message.add_reaction('✅')
    
    # الاستمرار في معالجة الأوامر الأخرى
    await bot.process_commands(message)
# ===== نهاية الحدث الجديد =====


@bot.tree.command()
async def voice(interaction: discord.Interaction, voice: str):
    """Set user's voice"""
    await interaction.response.defer()
    global_name = str(interaction.user.global_name)
    selected_voice = next((v for v in bot.voice_manager.voice_cache if v['name'].lower() == voice.lower()), None)
    if not selected_voice:
        await interaction.followup.send('لم يتم العثور على الصوت المطلوب.')
        return
    bot.voice_manager.user_voices[global_name] = selected_voice
    await interaction.followup.send(f"تم تعيين الصوت إلى {selected_voice['name']}")


@voice.autocomplete('voice')
async def voices_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    voices_name = [voice["name"] for voice in bot.voice_manager.voice_cache if voice["category"] == "cloned"]

    return [
        app_commands.Choice(name=voice_name, value=voice_name)
        for voice_name in voices_name if current.lower() in voice_name.lower()
    ]


@bot.tree.command()
async def volume(interaction: discord.Interaction, volume: int):
    """Changes the bot volume"""
    if interaction.guild.voice_client is None:
        await interaction.response.send_message("غير متصل بقناة صوتية.")
        return

    interaction.guild.voice_client.source.volume = volume / 100
    await interaction.response.send_message(f"تم تغيير مستوى الصوت إلى {volume}%")


@bot.tree.command()
async def stop(interaction: discord.Interaction):
    """Stops and disconnects the bot from voice channel"""
    if interaction.guild.voice_client and interaction.guild.voice_client.channel:
        await interaction.guild.voice_client.disconnect(force=True)
        await interaction.response.send_message("تم قطع الاتصال بقناة الصوت.")
    else:
        await interaction.response.send_message("غير متصل بقناة صوتية.")


async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == '__main__':
    asyncio.run(main())