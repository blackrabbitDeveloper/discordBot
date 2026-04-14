import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

KST = timezone(timedelta(hours=9))
UNIT_LABELS = {"m": "분", "h": "시간", "d": "일"}
UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class TimeUnit(discord.Enum):
    분 = "m"
    시간 = "h"
    일 = "d"


@dataclass
class ReminderEntry:
    id: int
    user_id: int
    message: str
    interval: int  # seconds
    repeat: bool
    task: asyncio.Task = field(repr=False)

    def label(self) -> str:
        if self.interval >= 86400:
            t, u = self.interval // 86400, "일"
        elif self.interval >= 3600:
            t, u = self.interval // 3600, "시간"
        else:
            t, u = self.interval // 60, "분"
        mode = "반복" if self.repeat else "일회"
        return f"`#{self.id}` [{mode}] {t}{u}마다 — {self.message}"


class Reminder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._entries: dict[int, list[ReminderEntry]] = {}  # user_id -> list
        self._next_id = 1

    def _add(self, entry: ReminderEntry):
        self._entries.setdefault(entry.user_id, []).append(entry)
        entry.task.add_done_callback(lambda _: self._remove(entry))

    def _remove(self, entry: ReminderEntry):
        entries = self._entries.get(entry.user_id, [])
        if entry in entries:
            entries.remove(entry)

    async def _run_reminder(self, user: discord.User, message: str, interval: int, repeat: bool):
        try:
            if repeat:
                while True:
                    await asyncio.sleep(interval)
                    await user.send(f"🔁 **반복 리마인더**: {message}")
            else:
                await asyncio.sleep(interval)
                await user.send(f"⏰ **리마인더**: {message}")
        except discord.Forbidden:
            pass
        except asyncio.CancelledError:
            pass

    @app_commands.command(name="remind", description="일정 시간 뒤에 DM으로 알림을 보냅니다")
    @app_commands.describe(
        time="시간 (숫자)",
        unit="단위",
        message="알림 내용",
        repeat="반복 여부 (기본: 한 번만)",
    )
    async def remind(
        self,
        interaction: discord.Interaction,
        time: int,
        unit: TimeUnit,
        message: str,
        repeat: bool = False,
    ):
        if time < 1:
            await interaction.response.send_message(
                "1 이상의 숫자를 입력해주세요.", ephemeral=True
            )
            return

        interval = time * UNIT_SECONDS[unit.value]
        task = asyncio.create_task(
            self._run_reminder(interaction.user, message, interval, repeat)
        )

        entry = ReminderEntry(
            id=self._next_id,
            user_id=interaction.user.id,
            message=message,
            interval=interval,
            repeat=repeat,
            task=task,
        )
        self._next_id += 1
        self._add(entry)

        mode = "반복" if repeat else "일회"
        when = datetime.now(KST) + timedelta(seconds=interval)
        await interaction.response.send_message(
            f"✅ `#{entry.id}` [{mode}] {time}{UNIT_LABELS[unit.value]}마다 ({when.strftime('%H:%M')}부터) DM 알림 설정됨\n내용: {message}",
            ephemeral=True,
        )

    @app_commands.command(name="remind-list", description="내 활성 리마인더 목록을 봅니다")
    async def remind_list(self, interaction: discord.Interaction):
        entries = self._entries.get(interaction.user.id, [])
        if not entries:
            await interaction.response.send_message(
                "활성 리마인더가 없습니다.", ephemeral=True
            )
            return

        lines = [e.label() for e in entries]
        await interaction.response.send_message(
            "**내 리마인더 목록**\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(name="remind-cancel", description="리마인더를 취소합니다")
    @app_commands.describe(id="취소할 리마인더 번호 (/remind-list에서 확인)")
    async def remind_cancel(self, interaction: discord.Interaction, id: int):
        entries = self._entries.get(interaction.user.id, [])
        target = next((e for e in entries if e.id == id), None)

        if not target:
            await interaction.response.send_message(
                f"리마인더 `#{id}`를 찾을 수 없습니다. `/remind-list`로 확인해주세요.",
                ephemeral=True,
            )
            return

        target.task.cancel()
        await interaction.response.send_message(
            f"❌ 리마인더 `#{id}` 취소됨 — {target.message}", ephemeral=True
        )

    async def cog_unload(self):
        for entries in self._entries.values():
            for entry in entries:
                entry.task.cancel()


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminder(bot))
