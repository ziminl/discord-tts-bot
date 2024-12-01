import os
import base64
import requests as requests
import time
from collections import deque

import discord
from discord.ext import tasks, commands


id_dir = os.path.dirname(__file__)

CHANNEL_ID = int(open(os.path.join(id_dir, "channel.txt"), "r").readline())
TOKEN = open(os.path.join(id_dir, "token.txt"), "r").readline()
VALID_VOICES = set([voice.strip() for voice in open(os.path.join(id_dir, "valid_voices.txt"), "r").readlines()])

INACTIVITY_TIMEOUT = 120
voice_text_channel = None

bot = commands.Bot(command_prefix=".", intents=discord.Intents().all())

user_profiles = {}
message_queue = deque()
is_playing = False
last_message_time = time.time()
last_talker = None


class UserProfile:
    def __init__(self, user, is_talking=True, voice="en_us_002", say_name=False):
        self.user = user
        self.is_talking = is_talking
        self.say_name = say_name
        self.voice = voice


class TTSMessage:
    def __init__(self, user, message):
        self.user = user
        self.message = message

    def play(self):
        global last_talker

        profile = user_profiles[self.user]

        content = self.message
        if profile.say_name and not last_talker == self.user:
            content = f"{self.user.display_name} said " + content

        audio_filename = f"voice_messages/out{int(time.time())}.mp3"
        create_tts_mp3(content, profile.voice, audio_filename)

        voice_client = discord.utils.get(bot.voice_clients, guild=self.user.guild)
        voice_client.play(discord.FFmpegPCMAudio(source=audio_filename), after=lambda x: advance_message_queue())
        last_talker = self.user


@bot.event
async def on_ready():
    global voice_text_channel
    voice_text_channel = bot.get_channel(CHANNEL_ID)
    activity_check.start()

    activity = discord.Game(name=".info")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    global is_playing

    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return

    user = message.author
    
    if user not in user_profiles:
        return
    
    profile = user_profiles[user]

    if not (user.voice and message.channel == voice_text_channel and profile.is_talking):
        return

    words = []
    for word in message.content.split():
        if word.startswith("http") or word.startswith(":"):
            continue
        words.append(word)
    stripped_message = " ".join(words)

    if len(stripped_message) == 0:
        return

    voice_client = discord.utils.get(bot.voice_clients, guild=user.guild)
    if not (voice_client and voice_client.is_connected()):
        await user.voice.channel.connect()

    message_queue.append(TTSMessage(user, stripped_message))

    if not is_playing:
        is_playing = True
        advance_message_queue()


@bot.command()
async def start(ctx):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user)
    user_profiles[user].is_talking = True
    await ctx.message.channel.send(f":green_circle: *{user}*, your TTS is now **ON**")


@bot.command()
async def stop(ctx):
    user = ctx.message.author
    user_profiles[user].is_talking = False
    await ctx.message.channel.send(f":red_circle: *{user}*, your TTS is now **OFF**")


@bot.command()
async def config(ctx, *args):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user, is_talking=False)
    if args[0] == "voice":
        if args[1] in VALID_VOICES:
            user_profiles[user].voice = args[1]
            await ctx.message.channel.send(f":pencil: *{user}*, your voice has been updated to `{args[1]}`")
        else:
            await ctx.message.channel.send(f":x: *{user}*, the voice you selected does not exist. "
                                           f"Search for available voices with `voicelist`")
    elif args[0] == "name":
        if args[1].lower() in ["true", "t", "yes", "y"]:
            user_profiles[user].say_name = True
        elif args[1].lower() in ["false", "f", "no", "n"]:
            user_profiles[user].say_name = False
        else:
            await ctx.message.channel.send(f":x: *{user}*, your choice could not be parsed as True or False")
            return
        await ctx.message.channel.send(f":pencil: *{user}*, "
                                       f"your name status has been updated to `{user_profiles[user].say_name}`")


@bot.command()
async def info(ctx):
    embed = discord.Embed(
        title="Help",
        description="Commands:\n " 
                    "`myprofile`: Shows your profile status with the bot\n"
                    "`info`: Prints list of commands and usage\n" 
                    "`voicelist`: Prints all voices currently supported\n"
                    "`config voice {voice}`: Updates your voice with the input you specify\n"
                    "`config name {yes/no}`: Updates if the bot will say your name before your messages\n"
                    "`start`: Allows the bot to start listening for your messages in the TTS channel\n"
                    "`stop`: Stops the bot from listening to your messages in the TTS channel\n"
    )
    await ctx.message.channel.send(embed=embed)


@bot.command()
async def voicelist(ctx):
    embed = discord.Embed(
        title="Voice List",
        description=", ".join(sorted([f"`{voice}`" for voice in VALID_VOICES]))
    )
    await ctx.message.channel.send(embed=embed)


@bot.command()
async def myprofile(ctx):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user, is_talking=False)
    embed = discord.Embed(
        title="Profile",
        description=(":green_circle: TTS Active" if user_profiles[user].is_talking else ":red_circle: TTS Inactive") +
                    f"\n:loud_sound: Voice: {user_profiles[user].voice}\n"
                    f":scroll: Say Name: {user_profiles[user].say_name}\n",
        color=0x5056c7
    )
    embed.set_author(name=user, icon_url=str(user.avatar_url))
    await ctx.message.channel.send(embed=embed)


@tasks.loop(seconds=2.0)
async def activity_check():
    voice = discord.utils.get(bot.voice_clients)
    if voice and voice.is_connected() and time.time() - last_message_time > INACTIVITY_TIMEOUT and not is_playing:
        await voice.disconnect()
        await voice_text_channel.send(f":pause_button: Left voice channel after {INACTIVITY_TIMEOUT} "
                                      f"seconds of inactivity. Talk to prompt the bot to rejoin")


def advance_message_queue():
    global is_playing, last_message_time
    if not message_queue:
        # Delete all temporary voice files
        for file in os.listdir(os.path.join(id_dir, "voice_messages")):
            os.remove(os.path.join(id_dir, "voice_messages/" + file))

        is_playing = False
    else:
        voice_request = message_queue.popleft()
        last_message_time = time.time()
        voice_request.play()


      # https://github.com/oscie57/tiktok-voice
def create_tts_mp3(text, voice, filename):
    url = f"https://api16-normal-useast5.us.tiktokv.com/media/api/text/speech/invoke/?" \
          f"text_speaker={voice}&req_text={text}&speaker_map_type=0"

    r = requests.post(url)

    b64d = base64.b64decode([r.json()["data"]["v_str"]][0])

    out = open(filename, "wb")
    out.write(b64d)
    out.close()


bot.run(TOKEN)
