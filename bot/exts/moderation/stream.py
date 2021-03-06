import datetime

import discord
from async_rediscache import RedisCache
from discord.ext import commands

from bot.bot import Bot
from bot.constants import Emojis, Guild, Roles, STAFF_ROLES, VideoPermission
from bot.converters import Expiry
from bot.utils.scheduling import Scheduler
from bot.utils.time import format_infraction_with_duration


class Stream(commands.Cog):
    """Grant and revoke streaming permissions from users."""

    # Stores tasks to remove streaming permission
    # User id : timestamp relation
    task_cache = RedisCache()

    def __init__(self, bot: Bot):
        self.bot = bot
        self.scheduler = Scheduler(self.__class__.__name__)
        self.reload_task = self.bot.loop.create_task(self._reload_tasks_from_redis())

    async def _remove_streaming_permission(self, schedule_user: discord.Member) -> None:
        """Remove streaming permission from Member."""
        await self._delete_from_redis(schedule_user.id)
        await schedule_user.remove_roles(discord.Object(Roles.video), reason="Streaming access revoked")

    async def _add_to_redis_cache(self, user_id: int, timestamp: float) -> None:
        """Adds 'task' to redis cache."""
        await self.task_cache.set(user_id, timestamp)

    async def _reload_tasks_from_redis(self) -> None:
        await self.bot.wait_until_guild_available()
        items = await self.task_cache.items()
        for key, value in items:
            member = await self.bot.get_guild(Guild.id).fetch_member(key)
            self.scheduler.schedule_at(datetime.datetime.utcfromtimestamp(value),
                                       key,
                                       self._remove_streaming_permission(member))

    async def _delete_from_redis(self, key: str) -> None:
        await self.task_cache.delete(key)

    @commands.command(aliases=("streaming",))
    @commands.has_any_role(*STAFF_ROLES)
    async def stream(
            self,
            ctx: commands.Context,
            user: discord.Member,
            duration: Expiry = None,
            *_
    ) -> None:
        """
        Temporarily grant streaming permissions to a user for a given duration.

        A unit of time should be appended to the duration.
        Units (∗case-sensitive):
        \u2003`y` - years
        \u2003`m` - months∗
        \u2003`w` - weeks
        \u2003`d` - days
        \u2003`h` - hours
        \u2003`M` - minutes∗
        \u2003`s` - seconds

        Alternatively, an ISO 8601 timestamp can be provided for the duration.
        """
        # if duration is none then calculate default duration
        if duration is None:
            now = datetime.datetime.utcnow()
            duration = now + datetime.timedelta(minutes=VideoPermission.default_permission_duration)

        # Check if user already has streaming permission
        already_allowed = any(Roles.video == role.id for role in user.roles)
        if already_allowed:
            await ctx.send(f"{Emojis.cross_mark} This user can already stream.")
            return

        # Schedule task to remove streaming permission from Member and add it to task cache
        self.scheduler.schedule_at(duration, user.id, self._remove_streaming_permission(user))
        await self._add_to_redis_cache(user.id, duration.timestamp())
        await user.add_roles(discord.Object(Roles.video), reason="Temporary streaming access granted")
        duration = format_infraction_with_duration(str(duration))
        await ctx.send(f"{Emojis.check_mark} {user.mention} can now stream until {duration}.")

    @commands.command(aliases=("pstream",))
    @commands.has_any_role(*STAFF_ROLES)
    async def permanentstream(
            self,
            ctx: commands.Context,
            user: discord.Member,
            *_
    ) -> None:
        """Permanently give user a streaming permission."""
        # Check if user already has streaming permission
        already_allowed = any(Roles.video == role.id for role in user.roles)
        if already_allowed:
            if user.id in self.scheduler:
                self.scheduler.cancel(user.id)
                await self._delete_from_redis(user.id)
                await ctx.send(f"{Emojis.check_mark} Moved temporary permission to permanent")
                return
            await ctx.send(f"{Emojis.cross_mark} This user can already stream.")
            return

        await user.add_roles(discord.Object(Roles.video), reason="Permanent streaming access granted")
        await ctx.send(f"{Emojis.check_mark} {user.mention} can now stream forever")

    @commands.command(aliases=("unstream", ))
    @commands.has_any_role(*STAFF_ROLES)
    async def revokestream(
            self,
            ctx: commands.Context,
            user: discord.Member
    ) -> None:
        """Take away streaming permission from a user."""
        # Check if user has the streaming permission to begin with
        allowed = any(Roles.video == role.id for role in user.roles)
        if allowed:
            # Cancel scheduled task to take away streaming permission to avoid errors
            if user.id in self.scheduler:
                self.scheduler.cancel(user.id)
            await self._remove_streaming_permission(user)
            await ctx.send(f"{Emojis.check_mark} Streaming permission taken from {user.display_name}.")
        else:
            await ctx.send(f"{Emojis.cross_mark} This user already can't stream.")

    def cog_unload(self) -> None:
        """Cancel all scheduled tasks."""
        self.reload_task.cancel()
        self.reload_task.add_done_callback(lambda _: self.scheduler.cancel_all())


def setup(bot: Bot) -> None:
    """Loads the Stream cog."""
    bot.add_cog(Stream(bot))
