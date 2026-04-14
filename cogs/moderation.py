import discord
from discord import app_commands
from discord.ext import commands


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="clear", description="채널의 메시지를 삭제합니다 (최대 100개)")
    @app_commands.describe(count="삭제할 메시지 수 (기본 100, 최대 100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear(self, interaction: discord.Interaction, count: int = 100):
        count = min(max(count, 1), 100)
        await interaction.response.defer(ephemeral=True)

        deleted = await interaction.channel.purge(limit=count)
        await interaction.followup.send(
            f"총 {len(deleted)}개의 메시지가 삭제되었습니다.", ephemeral=True
        )

    @clear.error
    async def clear_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "이 명령어를 사용할 권한이 없습니다.", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
