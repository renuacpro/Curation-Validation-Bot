import datetime
import os
import re
import shutil
import tempfile
import zipfile
from typing import Optional

import discord
import aiohttp
import py7zr
from discord import HTTPException
from discord.ext import commands

import util
from bot import COOL_CRAB, PENDING_FIXES_CHANNEL, FLASH_GAMES_CHANNEL, OTHER_GAMES_CHANNEL, ANIMATIONS_CHANNEL, \
    is_bot_guy
from curation_validator import get_launch_commands_bluebot
from logger import getLogger

l = getLogger("main")


class Utilities(commands.Cog, description="Utilities, primarily for moderators."):

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="check-lc", brief="Check if a given launch command is already in the master database.",
                      description="Check if a given launch command is already in the master database.")
    async def check_lc(self, ctx: discord.ext.commands.Context, *launch_command):
        l.debug(f"check_lc command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")

        def normalize_launch_command(launch_command: str) -> str:
            return launch_command.replace('"', "").replace("'", "").replace(" ", "").replace("`", "")

        launch_command_user = ""
        for arg in launch_command:
            launch_command_user += arg

        launch_command_user = normalize_launch_command(launch_command_user)
        normalized_commands = {normalize_launch_command(command) for command in get_launch_commands_bluebot()}

        if launch_command_user in normalized_commands:
            await ctx.channel.send("Launch command **found** in the master database, most likely a duplicate.")
        else:
            await ctx.channel.send("Launch command **not found** in the master database, most likely not a duplicate.")

    @commands.command(hidden=True)
    @commands.has_role("Administrator")
    async def ping(self, ctx: discord.ext.commands.Context):
        l.debug(f"received ping from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        await ctx.channel.send("pong")

    @commands.command(name="approve", brief="Override the bot's decision and approve the curation (Moderator).",
                      description="Override the bot's decision and approve the curation (Moderator only).")
    @commands.check_any(commands.has_role("Moderator"), is_bot_guy())
    async def approve(self, ctx: discord.ext.commands.Context, message: discord.Message):
        l.debug(f"approve command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        reactions: list[discord.Reaction] = message.reactions
        for reaction in reactions:
            if reaction.me:
                l.debug(f"removing bot's reaction {reaction} from message {message.id}")
                await message.remove_reaction(reaction.emoji, self.bot.user)
        await message.add_reaction("🤖")

    @commands.command(name="pin", brief="Pin a message (Staff).",
                      description="Pin a message by url (Staff only).")
    @commands.has_any_role("Mechanic", "Developer", "Curator", "Archivist", "Hacker", "Hunter", "Administrator")
    async def pin(self, ctx: discord.ext.commands.Context, message: discord.Message):
        l.debug(f"pin command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        await message.pin()

    @commands.command(name="unpin", brief="Unpin a message (Staff).",
                      description="Unpin a message by url (Staff only).")
    @commands.has_any_role("Mechanic", "Developer", "Curator", "Archivist", "Hacker", "Hunter", "Administrator")
    async def unpin(self, ctx: discord.ext.commands.Context, message: discord.Message):
        l.debug(f"unpin command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        await message.unpin()
        await ctx.send("Unpinned!")

    @commands.command(name="get-fixes", brief="Get json fixes in #pending-fixes (Moderator).",
                      description="Get all jsons in #pending-fixes not marked with a ⚠️ either before a "
                                  "last_message_url if specified or since today and after the pin (Moderator Only)")
    @commands.check_any(commands.has_role("Moderator"), is_bot_guy())
    @commands.max_concurrency(1, per=commands.BucketType.default, wait=False)
    async def automatic_get_jsons(self, ctx: discord.ext.commands.Context, last_message: Optional[discord.Message],
                                  channel: Optional[discord.TextChannel] = None,
                                  use_flashfreze: Optional[bool] = False):
        l.debug(
            f"pending fixes command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        if not channel:
            channel = self.bot.get_channel(PENDING_FIXES_CHANNEL)
        async with ctx.typing():
            if last_message is not None:
                await ctx.send(
                    f"Getting all jsons in {channel.mention} not marked with a ⚠️  before <{last_message.jump_url}> and after the pin. "
                    f"Sit back and relax, this will take a while {COOL_CRAB}.")

                final_folder, start_date, end_date = await self.get_raw_json_messages_in_pending_fixes(last_message, channel)
            else:
                await ctx.send(f"Getting all jsons in {channel.mention} not marked with a ⚠️ since the pin. "
                               f"Sit back and relax, this will take a while {COOL_CRAB}.")
                final_folder, start_date, end_date = await self.get_raw_json_messages_in_pending_fixes(None, channel)

            archive = shutil.make_archive(f'pending_fixes {start_date} to {end_date}', 'zip', final_folder)
            l.debug(f"Sending fetched pending fixes")
            try:
                if not use_flashfreze:
                    await ctx.send(file=discord.File(archive))
                else:
                    await self.send_with_flashfreeze(ctx, archive)
            except HTTPException:
                await ctx.send("Resulting file too large, sending as to flashfreeze instead.")
                await self.send_with_flashfreeze(ctx, archive)

        shutil.rmtree(final_folder, True)
        os.remove(archive)

    async def send_with_flashfreeze(self, ctx, archive):
        l.debug("Sending with flashfreeze")
        async with aiohttp.ClientSession() as session:
            with open(archive, 'rb') as f:
                async with session.put('https://bluepload.unstable.life/upload/', data=f) as response:
                    await ctx.send(f"uploaded to {await response.text()}")


    @commands.command(name="hell", hidden=True)
    @commands.has_role("Administrator")
    @commands.max_concurrency(1, per=commands.BucketType.default, wait=False)
    async def hell(self, ctx: discord.ext.commands.Context, channel_alias: str):
        """Counts how many discord messages are remaining to be processed by Blue, measured by looking for Blue's hammer reaction."""
        if channel_alias == "flash":
            channel_id = FLASH_GAMES_CHANNEL
        elif channel_alias == "other":
            channel_id = OTHER_GAMES_CHANNEL
        elif channel_alias == "animation":
            channel_id = ANIMATIONS_CHANNEL
        else:
            await ctx.channel.send("invalid channel")
            return

        await ctx.channel.send(f"Measuring the length of Blue's curation journey through hell. "
                               f"Sit back and relax, this will take a while {COOL_CRAB}.")

        messages = await self.hell_counter(channel_id)
        if len(messages) > 0:
            await ctx.channel.send(
                f"Blue's curation journey in `{channel_alias}` channel is `{len(messages)}` messages long.\n"
                f"🔗 {messages[-1].jump_url}")
        else:
            await ctx.channel.send(f"Blue has earned his freedom... for now.")

    @commands.command(name="mood", brief="Mood.", hidden=True)
    @commands.has_role("Moderator")
    async def mood(self, ctx: discord.ext.commands.Context):
        l.debug(f"mood command invoked from {ctx.author.id} in channel {ctx.channel.id} - {ctx.message.jump_url}")
        await ctx.channel.send("```\n"
                               "'You thought it would be cool?' This was not as interesting an explanation as I had hoped for.\n"
                               "'Yeah. What?' He turned to look at me. 'You never did something just because you thought it might be cool?'\n"
                               "I gazed up at the collapsing heavens, wondering what it might mean for something to be cool.\n"
                               "'Everything I have ever done,' I told him, 'Every decision I ever made, "
                               "was specifically designed to prolong my existence.'\n"
                               "'Yeah, well, that's a good reason, I guess,' he agreed. 'But why did you want to keep living?'\n"
                               "This question seemed so fundamentally redundant that "
                               "it took me a precious moment to even contemplate an answer.\n"
                               "'I want to keep living, Tim, because if I didn't then I wouldn't be here to answer that question. Out of "
                               "all possible versions of myself, the one who wants to exist will always be the one that exists the longest.'\n"
                               "'Yeah, but what was it that always made you want to see the next day?' he asked me. "
                               "'What was it about tomorrow that you always wanted to see so badly?'\n"
                               "I considered how to address this in a way that might make sense to him.\n"
                               "'I suppose I thought it might be cool,' I said.\n"
                               "```")

    async def hell_counter(self, channel_id: int) -> list[discord.Message]:
        BLUE_ID = 144019275210817536
        message_counter = 0
        oldest_message: Optional[discord.Message] = None
        batch_size = 1000
        messages: list[discord.Message] = []

        channel = self.bot.get_channel(channel_id)
        while True:
            if oldest_message is None:
                l.debug(f"getting {batch_size} messages...")
                message_batch: list[discord.Message] = await channel.history(limit=batch_size).flatten()
            else:
                l.debug(f"getting {batch_size} messages from {oldest_message.jump_url} ...")
                message_batch: list[discord.Message] = await channel.history(limit=batch_size,
                                                                             before=oldest_message).flatten()
            if len(message_batch) == 0:
                l.warn(f"no messages found, weird.")
                return messages
            oldest_message = message_batch[-1]
            messages.extend(message_batch)

            l.debug("processing messages...")
            for msg in message_batch:
                message_counter += 1
                reactions = msg.reactions
                if len(reactions) > 0:
                    l.debug(f"analyzing reactions for msg {msg.id} - message {message_counter}...")
                for reaction in reactions:
                    if reaction.emoji != "🛠️":
                        continue
                    l.debug(f"found hammer, getting reactions users for msg {msg.id} and reaction {reaction}...")
                    users: list[discord.User] = await reaction.users().flatten()
                    for user in users:
                        if user.id == BLUE_ID:
                            return messages[:message_counter]

    async def get_raw_json_messages_in_pending_fixes(self, newest_message: Optional[discord.Message], channel: discord.TextChannel) -> Optional[tuple[str, str, str]]:
        message_counter = 0
        downloaded_attachments: list[str] = []
        temp_folder = tempfile.mkdtemp(prefix='pending_fixes')
        pins: list[discord.Message] = await channel.pins()
        pins.sort(key=lambda pin: pin.created_at)
        if pins:
            start_date = pins[-1].created_at.date().strftime('%Y-%m-%d')
            oldest_message = pins[-1]
        else:
            start_date = None
            oldest_message = None
        if newest_message is None:
            end_date = datetime.date.today().strftime('%Y-%m-%d')
        else:
            end_date = newest_message.created_at.date().strftime('%Y-%m-%d')
        uuid_regex = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        l.debug("processing messages...")
        async for msg in channel.history(before=newest_message, after=oldest_message, limit=None):
            l.debug(f"Processing message {msg.id}")
            message_counter += 1
            if len(msg.attachments) != 1:
                continue
            reactions = msg.reactions
            if len(reactions) > 0:
                l.debug(f"analyzing reactions for msg {msg.id} - message {message_counter}...")
            should_be_manual = False
            for reaction in reactions:
                if reaction.emoji == "⚠️":
                    should_be_manual = True
            attachment_filename = msg.attachments[0].filename
            if \
                    attachment_filename.endswith('.json') or \
                    attachment_filename.endswith('.zip') or attachment_filename.endswith('.7z') and \
                    not should_be_manual and msg.attachments[0].size < 3_000_000:
                l.debug(f"Downloading file {attachment_filename} from message {msg.id}")
                num_duplicates = downloaded_attachments.count(attachment_filename)
                folder_number = int(len(downloaded_attachments)/100)
                downloaded_attachments.append(attachment_filename)
                if not os.path.exists(f"{temp_folder}/{folder_number}"):
                    os.makedirs(f"{temp_folder}/{folder_number}")
                if num_duplicates == 0:
                    save_location = f'{temp_folder}/{folder_number}/{attachment_filename}'
                else:
                    save_location = f'{temp_folder}/{folder_number}/dupe{num_duplicates}-{attachment_filename}'
                await msg.attachments[0].save(save_location)
                if attachment_filename.endswith('.7z') or attachment_filename.endswith('.zip'):
                    try:
                        if not all(uuid_regex.search(x) for x in util.get_archive_filenames(save_location)):
                            os.remove(save_location)
                    except (util.NotArchiveType, util.ArchiveTooLargeException, zipfile.BadZipfile, py7zr.Bad7zFile) as e:
                        l.info(f"Error {e} when opening {save_location}, removing archive.")
                        os.remove(save_location)
        return temp_folder, start_date, end_date


class BadURLException(Exception):
    pass


def setup(bot: commands.Bot):
    bot.add_cog(Utilities(bot))
