import os

from dotenv import load_dotenv

from bot import Bot

load_dotenv()

bot = Bot()
bot.run(os.getenv("DISCORD_TOKEN"))
