"""Discord bot that relays control-panel button presses to a Telegram chat."""
import asyncio
import logging

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


def main() -> None:
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
