"""Discord bot that relays control-panel button presses to a Telegram chat."""
import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import commands

from config import DISCORD_BOT_TOKEN
from telegram_service import TelegramAPIError, send_telegram_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("panel-bot")

intents = discord.Intents.default()
intents.message_content = True  # required for the "!dieukhien" prefix command

# action key -> (button label, Telegram HTML message)
ACTIONS = {
    "update_data": (
        "Cập nhật dữ liệu",
        "🔄 <b>Cập nhật dữ liệu:</b> Yêu cầu thực thi thành công.",
    ),
    "daily_report": (
        "Gửi báo cáo ngày",
        "📤 <b>Gửi báo cáo ngày:</b> Đang tổng hợp dữ liệu...",
    ),
    "weekly_report": (
        "Gửi báo cáo tuần",
        "📊 <b>Gửi báo cáo tuần:</b> Đang khởi tạo bản tin...",
    ),
    "test_teams": (
        "Test Teams",
        "⏳ <b>Test Teams:</b> Đang thực thi...\n✅ <b>Test Teams: hoàn tất</b>",
    ),
    "sys_status": (
        "Trạng thái",
        "📋 <b>Trạng thái hệ thống:</b> Đang hoạt động bình thường.",
    ),
}

# !spam config — repeats check-in message(s) on a timer until !stopspam or the safety cap is hit.
SPAM_MESSAGES = ["Kiểm tra kết nối."]
LOOP_INTERVAL_MS = 4000       # nghỉ giữa các vòng lặp
DELAY_BETWEEN_MSGS_MS = 1000  # nghỉ giữa từng tin trong 1 vòng (nếu SPAM_MESSAGES có nhiều tin)
SPAM_MAX_ITERATIONS = 720  # safety cap: max vòng lặp (~1 giờ ở tốc độ trên), in case !stopspam is forgotten

spam_task: asyncio.Task | None = None
spam_sent_count = 0
spam_started_at: float | None = None


class PanelBot(commands.Bot):
    """Bot subclass that owns a single pooled aiohttp session for the Telegram client."""

    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self.add_view(ControlPanelView())  # re-register persistent view after restarts
        try:
            synced = await self.tree.sync()
            logger.info("Synced %d slash command(s).", len(synced))
        except Exception:
            logger.exception("Failed to sync slash commands")

    async def close(self) -> None:
        if self.http_session is not None:
            await self.http_session.close()
            logger.info("Closed pooled aiohttp session.")
        await super().close()


bot = PanelBot()


async def handle_action(interaction: discord.Interaction, action_key: str) -> None:
    """Defer the interaction, forward the action to Telegram, then report the outcome."""
    # Defer immediately (ephemeral) so we never hit Discord's 3-second ack timeout.
    await interaction.response.defer(ephemeral=True)

    label, message = ACTIONS[action_key]
    session: aiohttp.ClientSession = interaction.client.http_session

    try:
        await send_telegram_message(session, message)
    except TelegramAPIError as exc:
        logger.warning("Telegram rejected '%s': %s", label, exc)
        await interaction.followup.send(
            f"❌ Telegram từ chối yêu cầu **{label}** (mã lỗi {exc.error_code}): {exc.description}",
            ephemeral=True,
        )
        return
    except asyncio.TimeoutError:
        logger.warning("Telegram request timed out for '%s'", label)
        await interaction.followup.send(
            f"❌ Gửi **{label}** thất bại: hết thời gian chờ kết nối tới Telegram.",
            ephemeral=True,
        )
        return
    except aiohttp.ClientConnectorError as exc:
        logger.warning("DNS/connection error for '%s': %s", label, exc)
        await interaction.followup.send(
            f"❌ Gửi **{label}** thất bại: không kết nối được tới Telegram (lỗi mạng/DNS).",
            ephemeral=True,
        )
        return
    except aiohttp.ClientError as exc:
        logger.warning("Network error for '%s': %s", label, exc)
        await interaction.followup.send(
            f"❌ Gửi **{label}** thất bại: lỗi mạng ({exc}).",
            ephemeral=True,
        )
        return
    except Exception:
        logger.exception("Unexpected error handling action '%s'", action_key)
        await interaction.followup.send(
            f"❌ Gửi **{label}** thất bại do lỗi không xác định. Vui lòng kiểm tra log.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(f"✅ Đã gửi **{label}** sang Telegram thành công.", ephemeral=True)


class ControlPanelView(discord.ui.View):
    """Persistent control panel view. custom_id values must stay stable across restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Cập nhật dữ liệu",
        emoji="🔄",
        style=discord.ButtonStyle.primary,
        custom_id="btn_update_data",
    )
    async def update_data(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_action(interaction, "update_data")

    @discord.ui.button(
        label="Gửi báo cáo ngày",
        emoji="📤",
        style=discord.ButtonStyle.primary,
        custom_id="btn_daily_report",
    )
    async def daily_report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_action(interaction, "daily_report")

    @discord.ui.button(
        label="Gửi báo cáo tuần",
        emoji="📊",
        style=discord.ButtonStyle.primary,
        custom_id="btn_weekly_report",
    )
    async def weekly_report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_action(interaction, "weekly_report")

    @discord.ui.button(
        label="Test Teams",
        emoji="🧪",
        style=discord.ButtonStyle.success,
        custom_id="btn_test_teams",
    )
    async def test_teams(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_action(interaction, "test_teams")

    @discord.ui.button(
        label="Trạng thái",
        emoji="📋",
        style=discord.ButtonStyle.secondary,
        custom_id="btn_sys_status",
    )
    async def sys_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await handle_action(interaction, "sys_status")


def build_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎛️ Bảng điều khiển",
        description="Nhấn một nút bên dưới để gửi thông báo trực tiếp sang Telegram.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="🔄 Cập nhật dữ liệu", value="Yêu cầu cập nhật dữ liệu mới nhất", inline=False)
    embed.add_field(name="📤 Gửi báo cáo ngày", value="Tổng hợp và gửi báo cáo trong ngày", inline=False)
    embed.add_field(name="📊 Gửi báo cáo tuần", value="Tổng hợp và gửi báo cáo trong tuần", inline=False)
    embed.add_field(name="🧪 Test Teams", value="Kiểm tra kết nối/luồng xử lý", inline=False)
    embed.add_field(name="📋 Trạng thái", value="Kiểm tra trạng thái hệ thống", inline=False)
    return embed


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
    logger.info("Telegram target chat configured; ready to relay panel actions.")


@bot.tree.command(name="dieukhien", description="Hiển thị bảng điều khiển gửi thông báo sang Telegram")
async def panel_slash(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=build_panel_embed(), view=ControlPanelView())


@bot.command(name="dieukhien")
async def panel_prefix(ctx: commands.Context) -> None:
    await ctx.send(embed=build_panel_embed(), view=ControlPanelView())


async def _spam_loop() -> None:
    """Send SPAM_MESSAGES to Telegram every LOOP_INTERVAL_MS until cancelled or capped."""
    global spam_task, spam_sent_count
    session: aiohttp.ClientSession = bot.http_session
    await bot.change_presence(activity=discord.Game(name="🔁 !spam đang chạy..."))
    cycle = 0
    try:
        while cycle < SPAM_MAX_ITERATIONS:
            cycle += 1
            for msg in SPAM_MESSAGES:
                spam_sent_count += 1
                try:
                    await send_telegram_message(session, msg)
                    logger.info("spam: sent message %d (cycle %d/%d)", spam_sent_count, cycle, SPAM_MAX_ITERATIONS)
                except Exception:
                    logger.exception("spam: failed to send message %d", spam_sent_count)
                await asyncio.sleep(DELAY_BETWEEN_MSGS_MS / 1000)
            await asyncio.sleep(LOOP_INTERVAL_MS / 1000)
        logger.info("spam: reached safety cap of %d cycles, stopping automatically", SPAM_MAX_ITERATIONS)
    except asyncio.CancelledError:
        logger.info("spam: stopped after %d message(s)", spam_sent_count)
        raise
    finally:
        spam_task = None
        await bot.change_presence(activity=None)


@bot.command(name="spam")
@commands.has_permissions(administrator=True)
async def cmd_spam(ctx: commands.Context) -> None:
    global spam_task, spam_sent_count, spam_started_at
    if spam_task is not None and not spam_task.done():
        elapsed = int(time.time() - spam_started_at) if spam_started_at else 0
        await ctx.send(
            f"⚠️ Đang có tiến trình `!spam` chạy rồi — đã gửi **{spam_sent_count}/{SPAM_MAX_ITERATIONS}** lần, "
            f"chạy được {elapsed}s. Dùng `!stopspam` để dừng."
        )
        return
    spam_sent_count = 0
    spam_started_at = time.time()
    spam_task = bot.loop.create_task(_spam_loop())
    await ctx.send(
        f"✅ Đã bắt đầu — mỗi tin cách nhau {DELAY_BETWEEN_MSGS_MS}ms, mỗi vòng cách nhau {LOOP_INTERVAL_MS}ms "
        f"(tự dừng sau tối đa {SPAM_MAX_ITERATIONS} vòng). Gõ `!spam` lại để xem tiến độ, "
        f"`!stopspam` để dừng bất cứ lúc nào. Trạng thái bot (presence) cũng sẽ hiện '🔁 !spam đang chạy...' trong lúc chạy."
    )


@cmd_spam.error
async def cmd_spam_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Chỉ admin mới dùng được lệnh này.")
    else:
        raise error


@bot.command(name="stopspam")
@commands.has_permissions(administrator=True)
async def cmd_stopspam(ctx: commands.Context) -> None:
    global spam_task
    if spam_task is None or spam_task.done():
        await ctx.send("ℹ️ Hiện không có tiến trình `!spam` nào đang chạy.")
        return
    spam_task.cancel()
    await ctx.send(f"🛑 Đã dừng `!spam` sau khi gửi {spam_sent_count} lần.")


@cmd_stopspam.error
async def cmd_stopspam_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Chỉ admin mới dùng được lệnh này.")
    else:
        raise error


def main() -> None:
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
