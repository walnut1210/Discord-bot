import json
import logging
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_FILE = DATA_DIR / "reaction_roles.json"


def load_panels() -> dict[str, dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with DATA_FILE.open(encoding="utf-8") as fp:
            value = json.load(fp)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        log.exception("Could not load reaction role data")
        return {}


def save_panels(panels: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = DATA_FILE.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as fp:
        json.dump(panels, fp, ensure_ascii=False, indent=2)
    temp.replace(DATA_FILE)


def make_embed(panel: dict[str, Any]) -> discord.Embed:
    rows = [f"{emoji} — <@&{item['role_id']}>" for emoji, item in panel["roles"].items()]
    description = panel["description"]
    if rows:
        description += "\n\n" + "\n".join(rows)
    embed = discord.Embed(title=panel["title"], description=description, color=discord.Color.blurple())
    embed.set_footer(text="リアクションを付けるとロール付与、外すと解除")
    return embed


class RolePanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.panels = load_panels()
    @app_commands.command(name="role-panel-create", description="ロール選択パネルを作成")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def create_command(self, interaction: discord.Interaction, channel: discord.TextChannel,
                             title: app_commands.Range[str, 1, 100] = "ロール選択",
                             description: app_commands.Range[str, 1, 1000] = "希望するロールのリアクションを選択してください。"):
        if not interaction.permissions.manage_guild:
            return await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
        panel = {"guild_id": interaction.guild_id, "channel_id": channel.id, "title": title,
                 "description": description, "roles": {}}
        try:
            message = await channel.send(embed=make_embed(panel))
        except discord.HTTPException:
            return await interaction.response.send_message("パネルを送信できません。Botの権限を確認してください。", ephemeral=True)
        panel["message_id"] = message.id
        self.panels[str(message.id)] = panel
        save_panels(self.panels)
        await interaction.response.send_message(f"パネルを作成しました: {message.jump_url}\nメッセージID: {message.id}", ephemeral=True)

    @app_commands.command(name="role-panel-add", description="パネルに絵文字とロールを登録")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def add_command(self, interaction: discord.Interaction, message_id: str,
                          role: discord.Role, emoji: app_commands.Range[str, 1, 64]):
        panel = self.panels.get(message_id.strip())
        if not interaction.permissions.manage_guild or not panel or panel["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("権限がないか、パネルが見つかりません。", ephemeral=True)
        guild = interaction.guild
        me = guild.me if guild else None
        if me is None or role.is_default() or role.managed or not me.guild_permissions.manage_roles or role >= me.top_role:
            return await interaction.response.send_message("Botのロール管理権限またはロール階層を確認してください。", ephemeral=True)
        try:
            parsed = discord.PartialEmoji.from_str(emoji.strip())
            channel = self.bot.get_channel(panel["channel_id"]) or await self.bot.fetch_channel(panel["channel_id"])
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(parsed)
            panel["roles"][str(parsed)] = {"role_id": role.id}
            await message.edit(embed=make_embed(panel))
            save_panels(self.panels)
        except (discord.HTTPException, ValueError, AttributeError):
            log.exception("Failed to register reaction role")
            return await interaction.response.send_message("登録に失敗しました。メッセージとBot権限を確認してください。", ephemeral=True)
        await interaction.response.send_message(f"{parsed} → {role.mention} を登録しました。", ephemeral=True)

    @app_commands.command(name="role-panel-remove", description="パネルから絵文字とロールの登録を削除")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def remove_command(self, interaction: discord.Interaction, message_id: str,
                             emoji: app_commands.Range[str, 1, 64]):
        panel = self.panels.get(message_id.strip())
        if not interaction.permissions.manage_guild or not panel or panel["guild_id"] != interaction.guild_id:
            return await interaction.response.send_message("権限がないか、パネルが見つかりません。", ephemeral=True)
        key = str(discord.PartialEmoji.from_str(emoji.strip()))
        if key not in panel["roles"]:
            return await interaction.response.send_message("登録が見つかりません。", ephemeral=True)
        try:
            channel = self.bot.get_channel(panel["channel_id"]) or await self.bot.fetch_channel(panel["channel_id"])
            message = await channel.fetch_message(int(message_id))
            # Remove only the bot's own reaction; clear_reaction() would erase every user's reaction.
            if self.bot.user is not None:
                await message.remove_reaction(discord.PartialEmoji.from_str(key), self.bot.user)
            del panel["roles"][key]
            await message.edit(embed=make_embed(panel))
            save_panels(self.panels)
        except discord.HTTPException:
            log.exception("Failed to remove reaction role")
            return await interaction.response.send_message("削除に失敗しました。", ephemeral=True)
        await interaction.response.send_message("登録を削除しました。", ephemeral=True)

    async def member_for(self, guild: discord.Guild, user_id: int):
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def process(self, payload: discord.RawReactionActionEvent, adding: bool):
        if self.bot.user is None or payload.user_id == self.bot.user.id or payload.guild_id is None:
            return
        panel = self.panels.get(str(payload.message_id))
        if not panel or panel["guild_id"] != payload.guild_id:
            return
        entry = panel["roles"].get(str(payload.emoji))
        guild = self.bot.get_guild(payload.guild_id)
        if not entry or guild is None:
            return
        member = await self.member_for(guild, payload.user_id)
        role = guild.get_role(entry["role_id"])
        me = guild.me
        if member is None or member.bot or role is None or me is None:
            return
        if not me.guild_permissions.manage_roles or role >= me.top_role:
            log.warning("Cannot manage configured role %s", entry["role_id"])
            return
        try:
            if adding and role not in member.roles:
                await member.add_roles(role, reason="Reaction role selected")
            elif not adding and role in member.roles:
                await member.remove_roles(role, reason="Reaction role deselected")
        except discord.HTTPException:
            log.exception("Could not update role %s", role.id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        await self.process(payload, True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.process(payload, False)


async def setup(bot: commands.Bot):
    await bot.add_cog(RolePanel(bot))
