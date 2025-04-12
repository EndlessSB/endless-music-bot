import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import os
import asyncio
import hashlib
from dotenv import load_dotenv

CACHE_DIR = "cache"
os.makedirs(CACHE_DIR, exist_ok=True)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

load_dotenv()
token = os.getenv('token')

voice_clients = {}      # server_id -> voice_client
persistent_vc = {}      # server_id -> channel_id
idle_tasks = {}         # server_id -> asyncio.Task
IDLE_TIMEOUT = 300      # 5 minutes


def get_cache_path(url):
    key = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.mp3")


async def download_song(search_term):
    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "default_search": "ytsearch1",
        "outtmpl": "%(id)s.%(ext)s",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_term, download=False)
        url = info["entries"][0]["webpage_url"] if "entries" in info else info["webpage_url"]
        cached_path = get_cache_path(url)
        if os.path.exists(cached_path):
            return cached_path, info["title"]

        info = ydl.extract_info(url, download=True)
        original_filename = ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp3"
        os.rename(original_filename, cached_path)
        return cached_path, info["title"]


def start_idle_timer(guild_id):
    async def timeout_disconnect():
        await asyncio.sleep(IDLE_TIMEOUT)
        vc = voice_clients.get(guild_id)
        if vc and vc.is_connected() and not vc.is_playing():
            await vc.disconnect()
            voice_clients.pop(guild_id, None)
            print(f"Disconnected from VC in guild {guild_id} due to inactivity.")

            if guild_id in persistent_vc:
                try:
                    channel = discord.utils.get(vc.guild.voice_channels, id=persistent_vc[guild_id])
                    if channel:
                        new_vc = await channel.connect()
                        voice_clients[guild_id] = new_vc
                        print(f"Rejoined 24/7 VC in guild {guild_id}.")
                except Exception as e:
                    print(f"Failed to rejoin 24/7 VC: {e}")

    task = idle_tasks.get(guild_id)
    if task:
        task.cancel()
    idle_tasks[guild_id] = asyncio.create_task(timeout_disconnect())


@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")


@bot.tree.command(name="play", description="Play a song in your voice channel.")
@app_commands.describe(song="The song name or URL to play.")
async def play(interaction: discord.Interaction, song: str):
    await interaction.response.defer()

    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("❌ You must be in a voice channel to play music.")
        return

    voice_channel = interaction.user.voice.channel
    guild_id = interaction.guild.id

    if guild_id in voice_clients and voice_clients[guild_id].is_connected():
        vc = voice_clients[guild_id]
    else:
        vc = await voice_channel.connect()
        voice_clients[guild_id] = vc

    try:
        file_path, title = await download_song(song)
    except Exception as e:
        await interaction.followup.send(f"❌ Failed to play the song: {e}")
        return

    if vc.is_playing():
        vc.stop()

    vc.play(discord.FFmpegPCMAudio(file_path), after=lambda e: start_idle_timer(guild_id))
    await interaction.followup.send(f"🎶 Now playing: **{title}**")

    start_idle_timer(guild_id)


@bot.tree.command(name="stop", description="Stop the music and leave the voice channel.")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id in voice_clients:
        vc = voice_clients[guild_id]
        if vc.is_playing():
            vc.stop()
        await vc.disconnect()
        voice_clients.pop(guild_id, None)
        await interaction.response.send_message("🛑 Stopped playing and left the voice channel.")
    else:
        await interaction.response.send_message("❌ I'm not in a voice channel.")


@bot.tree.command(name="247", description="Make the bot stay in your current voice channel 24/7.")
async def stay_247(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ You must be in a voice channel to enable 24/7 mode.")
        return

    guild_id = interaction.guild.id
    voice_channel = interaction.user.voice.channel
    persistent_vc[guild_id] = voice_channel.id

    if guild_id in voice_clients and voice_clients[guild_id].is_connected():
        vc = voice_clients[guild_id]
        if vc.channel.id != voice_channel.id:
            await vc.disconnect()
            vc = await voice_channel.connect()
            voice_clients[guild_id] = vc
    else:
        vc = await voice_channel.connect()
        voice_clients[guild_id] = vc

    await interaction.response.send_message(f"✅ Set **{voice_channel.name}** as the 24/7 voice channel.")


bot.run(token)
