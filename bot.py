import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
MOD_ROLE_ID = int(os.getenv("MOD_ROLE_ID", "0"))
TICKET_CATEGORY_ID = int(os.getenv("TICKET_CATEGORY_ID", "0"))
GIVEAWAY_EMOJI = os.getenv("GIVEAWAY_EMOJI", "🎉")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "giveaways.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("discord-bot")


def load_giveaways() -> dict[str, dict[str, Any]]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with DATA_FILE.open(encoding="utf-8") as file:
            value = json.load(file)
            return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        log.exception("Could not load giveaway data")
        return {}


def save_giveaways(data: dict[str, dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = DATA_FILE.with_suffix(".json.tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    temp.replace(DATA_FILE)


def giveaway_embed(data: dict[str, Any], ended: bool = False) -> discord.Embed:
    text = (
        f"**賞品:** {data['prize']}\n"
        f"**当選人数:** {data['winner_count']} 人\n"
        f"**参加方法:** このメッセージに {data['emoji']} を付けてください。\n"
        "リアクションを外すと参加を辞退できます。\n\n"
    )
    text += "**ステータス:** 終了" if ended else f"**締切:** <t:{data['ends_at']}:F>（<t:{data['ends_at']}:R>）"
    embed = discord.Embed(
        title="🎉 抽選終了" if ended else "🎉 Giveaway",
        description=text,
        color=discord.Color.dark_grey() if ended else discord.Color.blurple(),
    )
    embed.set_footer(text=f"Giveaway ID: {data.get('message_id', '作成中')}")
    return embed


class GiveawayGroup(app_commands.Group):
    def __init__(self) -> None:
        super().__init__(
            name="giveaway",
            description="抽選企画を管理します",
            default_permissions=discord.Permissions(manage_guild=True),
        )

    @app_commands.command(name="create", description="抽選企画を作成します")
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 100],
        duration: app_commands.Range[int, 1, 43200],
        winners: app_commands.Range[int, 1, 20],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("開催先にはテキストチャンネルを指定してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        now = int(discord.utils.utcnow().timestamp())
        data: dict[str, Any] = {
            "guild_id": interaction.guild_id,
            "channel_id": target.id,
            "prize": prize,
            "winner_count": winners,
            "emoji": GIVEAWAY_EMOJI,
            "created_at": now,
            "ends_at": now + duration * 60,
            "ended": False,
            "winner_ids": [],
        }
        try:
            message = await target.send(embed=giveaway_embed(data))
            data["message_id"] = message.id
            interaction.client.giveaways[str(message.id)] = data
            save_giveaways(interaction.client.giveaways)
            await message.add_reaction(GIVEAWAY_EMOJI)
            interaction.client.schedule_giveaway(str(message.id))
            await interaction.followup.send(
                f"抽選を作成しました: {target.mention}\nメッセージID: {message.id}",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception("Giveaway creation failed")
            await interaction.followup.send("抽選の作成に失敗しました。Botの権限を確認してください。", ephemeral=True)

    @app_commands.command(name="reroll", description="終了した抽選を再抽選します（過去の当選者は除外）")
    @app_commands.guild_only()
    async def reroll(self, interaction: discord.Interaction, message_id: str) -> None:
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        message_id = message_id.strip()
        if not re.fullmatch(r"\\d{17,20}", message_id):
            await interaction.response.send_message("メッセージIDの形式が正しくありません。", ephemeral=True)
            return
        data = interaction.client.giveaways.get(message_id)
        if not data or data.get("guild_id") != interaction.guild_id:
            await interaction.response.send_message("このサーバーの抽選が見つかりません。", ephemeral=True)
            return
        if not data.get("ended"):
            await interaction.response.send_message("開催中の抽選は再抽選できません。先に終了してください。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await interaction.client.reroll_giveaway(message_id)
        if not result:
            await interaction.followup.send("抽選メッセージを取得できませんでした。", ephemeral=True)
        elif "error" in result:
            await interaction.followup.send(result["error"], ephemeral=True)
        else:
            await interaction.followup.send(
                f"再抽選が完了しました。再抽選: {result['reroll_count']} 回目 / 当選者: {len(result['winners'])} 人",
                ephemeral=True,
            )

    @app_commands.command(name="end", description="抽選を終了して当選者を決定します")
    @app_commands.guild_only()
    async def end(self, interaction: discord.Interaction, message_id: str) -> None:
        if not interaction.permissions.manage_guild:
            await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
            return
        message_id = message_id.strip()
        if not re.fullmatch(r"\d{17,20}", message_id):
            await interaction.response.send_message("メッセージIDの形式が正しくありません。", ephemeral=True)
            return
        data = interaction.client.giveaways.get(message_id)
        if not data or data.get("ended"):
            await interaction.response.send_message("開催中の抽選が見つかりません。", ephemeral=True)
            return
        if data["guild_id"] != interaction.guild_id:
            await interaction.response.send_message("このサーバーの抽選ではありません。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = await interaction.client.finish_giveaway(message_id, "主催者による手動終了")
        if result is None:
            await interaction.followup.send("抽選を終了できませんでした。", ephemeral=True)
        else:
            await interaction.followup.send(
                f"抽選を終了しました。参加者: {result['entrants']} 人 / 当選者: {len(result['winners'])} 人",
                ephemeral=True,
            )


def clean_channel_name(username: str) -> str:
    name = re.sub(r"[^a-z0-9-]", "-", username.lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return (name or "user")[:80]


def get_ticket_creator(channel: discord.abc.GuildChannel) -> int | None:
    match = re.fullmatch(r"creator:(\d+)", getattr(channel, "topic", None) or "")
    return int(match.group(1)) if match else None


def can_close_ticket(member: discord.Member, channel: discord.TextChannel) -> bool:
    has_mod_role = bool(MOD_ROLE_ID) and any(role.id == MOD_ROLE_ID for role in member.roles)
    return member.id == get_ticket_creator(channel) or has_mod_role or member.guild_permissions.manage_channels


async def close_ticket(interaction: discord.Interaction) -> None:
    channel, member = interaction.channel, interaction.user
    if not isinstance(channel, discord.TextChannel) or not isinstance(member, discord.Member):
        await interaction.response.send_message("チケットチャンネル内で実行してください。", ephemeral=True)
        return
    if get_ticket_creator(channel) is None:
        await interaction.response.send_message("このチャンネルはチケットとして認識できません。", ephemeral=True)
        return
    if not can_close_ticket(member, channel):
        await interaction.response.send_message("チケットを閉じる権限がありません。", ephemeral=True)
        return
    await interaction.response.send_message("チケットを閉じます。このチャンネルは5秒後に削除されます。")
    await asyncio.sleep(5)
    try:
        await channel.delete(reason=f"Ticket closed by {member} ({member.id})")
    except discord.HTTPException:
        log.exception("Failed to delete ticket channel")


class CloseView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="チケットを閉じる", style=discord.ButtonStyle.danger, custom_id="ticket:close", emoji="🔒")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await close_ticket(interaction)


class TicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="チケットを作成", style=discord.ButtonStyle.primary, custom_id="ticket:create", emoji="🎫")
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("サーバー内でのみ利用できます。", ephemeral=True)
            return
        category = guild.get_channel(TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("チケットカテゴリが見つかりません。設定を確認してください。", ephemeral=True)
            return
        for existing in category.text_channels:
            if get_ticket_creator(existing) == member.id:
                await interaction.response.send_message(f"すでにチケットがあります: {existing.mention}", ephemeral=True)
                return
        moderator_role = guild.get_role(MOD_ROLE_ID)
        bot_member = guild.me
        if moderator_role is None or bot_member is None:
            await interaction.response.send_message("モデレーターロールまたはBotのメンバー情報が見つかりません。", ephemeral=True)
            return
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True),
            moderator_role: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True),
            bot_member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
        }
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            ticket_channel = await guild.create_text_channel(
                name=f"ticket-{clean_channel_name(member.name)}",
                category=category,
                topic=f"creator:{member.id}",
                overwrites=overwrites,
                reason=f"Ticket opened by {member} ({member.id})",
            )
            embed = discord.Embed(
                title="お問い合わせ",
                description=f"{member.mention} さんのチケットを作成しました。\nこちらにお問い合わせ内容を送信してください。\n対応が終了したら、下のボタンからチケットを閉じられます。",
                color=discord.Color.blurple(),
            )
            await ticket_channel.send(content=member.mention, embed=embed, view=CloseView())
            await interaction.followup.send(f"チケットを作成しました: {ticket_channel.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("チャンネルを作成できませんでした。Botのチャンネル管理権限を確認してください。", ephemeral=True)
        except discord.HTTPException:
            log.exception("Failed to create ticket channel")
            await interaction.followup.send("チケットの作成中にエラーが発生しました。", ephemeral=True)


class CombinedBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.guild_messages = True
        intents.guild_reactions = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.giveaways: dict[str, dict[str, Any]] = load_giveaways()
        self.giveaway_tasks: dict[str, asyncio.Task[None]] = {}
        self.giveaway_locks: dict[str, asyncio.Lock] = {}

    async def setup_hook(self) -> None:
        self.add_view(TicketView())
        self.add_view(CloseView())
        self.tree.add_command(GiveawayGroup())
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        for message_id, data in self.giveaways.items():
            if not data.get("ended"):
                self.schedule_giveaway(message_id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def reroll_giveaway(self, message_id: str) -> dict[str, Any] | None:
        lock = self.giveaway_locks.setdefault(message_id, asyncio.Lock())
        async with lock:
            data = self.giveaways.get(message_id)
            if not data or not data.get("ended"):
                return None

            try:
                channel = self.get_channel(int(data["channel_id"])) or await self.fetch_channel(int(data["channel_id"]))
            except discord.HTTPException:
                return None
            if not isinstance(channel, discord.TextChannel):
                return None

            try:
                message = await channel.fetch_message(int(message_id))
            except discord.HTTPException:
                return None

            reaction = next((r for r in message.reactions if str(r.emoji) == data["emoji"]), None)
            if reaction is None:
                return {"error": "参加リアクションが見つかりません。"}

            entrants: dict[int, discord.User] = {}
            try:
                async for user in reaction.users(limit=None):
                    if not user.bot:
                        entrants[user.id] = user
            except discord.HTTPException:
                log.exception("Could not fetch giveaway participants for reroll")
                return {"error": "参加者を取得できませんでした。Botの権限を確認してください。"}

            history = set(data.get("winner_history", data.get("winner_ids", [])))
            candidates = [user for user in entrants.values() if user.id not in history]
            if not candidates:
                return {"error": "過去の当選者を除外すると、再抽選できる参加者がいません。"}

            random.SystemRandom().shuffle(candidates)
            winners = candidates[:int(data["winner_count"])]
            winner_ids = [user.id for user in winners]
            data["winner_history"] = list(history.union(winner_ids))
            data["winner_ids"] = winner_ids
            data["reroll_count"] = int(data.get("reroll_count", 0)) + 1
            data["last_rerolled_at"] = int(discord.utils.utcnow().timestamp())
            save_giveaways(self.giveaways)

            winner_text = ", ".join(user.mention for user in winners)
            try:
                await channel.send(
                    f"🔄 **再抽選結果：{data['prize']}**（再抽選 {data['reroll_count']} 回目）\\n"
                    f"当選者: {winner_text}\\n"
                    f"※過去の当選者は再選出対象から除外しています。",
                    allowed_mentions=discord.AllowedMentions(users=winners),
                )
            except discord.HTTPException:
                log.exception("Could not send giveaway reroll result")
                return {"error": "当選者は保存されましたが、結果メッセージを送信できませんでした。"}
            return {"winners": winners, "remaining": len(candidates) - len(winners), "reroll_count": data["reroll_count"]}

    def schedule_giveaway(self, message_id: str) -> None:
        old = self.giveaway_tasks.get(message_id)
        if old and not old.done():
            old.cancel()
        self.giveaway_tasks[message_id] = asyncio.create_task(self._wait_and_finish(message_id))

    async def _wait_and_finish(self, message_id: str) -> None:
        data = self.giveaways.get(message_id)
        if not data or data.get("ended"):
            return
        remaining = max(0, int(data["ends_at"]) - int(discord.utils.utcnow().timestamp()))
        await asyncio.sleep(remaining)
        await self.finish_giveaway(message_id, "開催期限")

    async def finish_giveaway(self, message_id: str, reason: str = "期限到来") -> dict[str, Any] | None:
        lock = self.giveaway_locks.setdefault(message_id, asyncio.Lock())
        async with lock:
            data = self.giveaways.get(message_id)
            if not data or data.get("ended"):
                return None
            task = self.giveaway_tasks.get(message_id)
            if task and task is not asyncio.current_task() and not task.done():
                task.cancel()
            self.giveaway_tasks.pop(message_id, None)
            try:
                channel = self.get_channel(int(data["channel_id"])) or await self.fetch_channel(int(data["channel_id"]))
            except discord.HTTPException:
                channel = None
            message = None
            if isinstance(channel, discord.TextChannel):
                try:
                    message = await channel.fetch_message(int(message_id))
                except discord.HTTPException:
                    message = None
            entrants: dict[int, discord.User] = {}
            if message is not None:
                reaction = next((r for r in message.reactions if str(r.emoji) == data["emoji"]), None)
                if reaction is not None:
                    try:
                        async for user in reaction.users(limit=None):
                            if not user.bot:
                                entrants[user.id] = user
                    except discord.HTTPException:
                        log.exception("Could not fetch giveaway participants")
            participants = list(entrants.values())
            random.SystemRandom().shuffle(participants)
            winners = participants[:int(data["winner_count"])]
            data["ended"] = True
            data["ended_at"] = int(discord.utils.utcnow().timestamp())
            data["winner_ids"] = [user.id for user in winners]
            data["winner_history"] = list(set(data.get("winner_history", [])) | set(data["winner_ids"]))
            save_giveaways(self.giveaways)
            if message is not None:
                try:
                    await message.edit(embed=giveaway_embed(data, ended=True))
                except discord.HTTPException:
                    log.exception("Could not edit giveaway message")
            winner_text = ", ".join(user.mention for user in winners) if winners else "当選者なし（参加者がいませんでした）"
            if channel is not None:
                try:
                    await channel.send(
                        f"🎊 **抽選結果：{data['prize']}**\n当選者: {winner_text}\n（{reason}）",
                        allowed_mentions=discord.AllowedMentions(users=winners),
                    )
                except discord.HTTPException:
                    log.exception("Could not send giveaway result")
            return {"entrants": len(participants), "winners": winners}

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if self.user and payload.user_id == self.user.id:
            return
        data = self.giveaways.get(str(payload.message_id))
        if not data or data.get("ended") or str(payload.emoji) != data["emoji"]:
            return
        if int(discord.utils.utcnow().timestamp()) >= int(data["ends_at"]):
            await self.finish_giveaway(str(payload.message_id), "開催期限")


bot = CombinedBot()


@bot.tree.command(name="ticket-panel", description="チケット作成パネルを設置します。")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def ticket_panel(interaction: discord.Interaction) -> None:
    if not interaction.permissions.manage_guild:
        await interaction.response.send_message("サーバー管理権限が必要です。", ephemeral=True)
        return
    if not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        await interaction.response.send_message("テキストチャンネル内で実行してください。", ephemeral=True)
        return
    embed = discord.Embed(
        title="お問い合わせ",
        description="お問い合わせがある場合は、下のボタンを押してチケットを作成してください。\n作成したチケットは、本人とサポート担当者だけが閲覧できます。",
        color=discord.Color.blurple(),
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("チケットパネルを設置しました。", ephemeral=True)


@bot.tree.command(name="ticket-close", description="現在のチケットを閉じます。")
@app_commands.guild_only()
async def ticket_close(interaction: discord.Interaction) -> None:
    await close_ticket(interaction)


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN が設定されていません。.env.example を参考に設定してください。")
    if not MOD_ROLE_ID or not TICKET_CATEGORY_ID:
        raise RuntimeError("MOD_ROLE_ID と TICKET_CATEGORY_ID を設定してください。")
    bot.run(TOKEN)
