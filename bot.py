import discord
from discord.ext import commands


class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.load_extension("cogs.moderation")
        await self.load_extension("cogs.reminder")
        await self.load_extension("cogs.stock")
        await self.load_extension("cogs.etf")
        await self.load_extension("cogs.market")
        await self.load_extension("cogs.crypto")
        await self.load_extension("cogs.exchange")
        await self.load_extension("cogs.news")
        await self.load_extension("cogs.help")
        await self.load_extension("cogs.fundamental")
        await self.load_extension("cogs.calculator")
        await self.load_extension("cogs.calendar")
        await self.load_extension("cogs.autopost")
        await self.tree.sync()

    async def on_ready(self):
        print(f"{self.user} 봇이 시작되었습니다!")
        print(f"봇 ID: {self.user.id}")
        print(f"서버 목록: {[guild.name for guild in self.guilds]}")
