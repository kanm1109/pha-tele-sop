"""Discord bot that relays control-panel button presses to a Telegram chat."""
import asyncio
import logging
import time

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import set_key

from config import ALERT_CHANNEL_ID, DISCORD_BOT_TOKEN, DOTENV_PATH
from telegram_service import TelegramAPIError, get_me, get_token, send_telegram_message, set_token

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
SPAM_MESSAGES = ["."]
LOOP_INTERVAL_MS = 50       # nghỉ giữa các vòng lặp
DELAY_BETWEEN_MSGS_MS = 10  # nghỉ giữa từng tin trong 1 vòng (nếu SPAM_MESSAGES có nhiều tin)
SPAM_MAX_ITERATIONS = 1000  # safety cap: max vòng lặp (~1 giờ ở tốc độ trên), in case !stopspam is forgotten

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
        check_telegram_token.start()

    async def close(self) -> None:
        check_telegram_token.cancel()
        if self.http_session is not None:
            await self.http_session.close()
            logger.info("Closed pooled aiohttp session.")
        await super().close()


bot = PanelBot()

# Periodic health check — the bot owner can rotate TELEGRAM_BOT_TOKEN without warning.
TOKEN_CHECK_INTERVAL_MINUTES = 15
telegram_token_ok = True


async def _alert_admin(text: str) -> None:
    """Post a message to ALERT_CHANNEL_ID, if configured. Silently no-ops otherwise."""
    if not ALERT_CHANNEL_ID:
        return
    try:
        channel = bot.get_channel(int(ALERT_CHANNEL_ID)) or await bot.fetch_channel(int(ALERT_CHANNEL_ID))
        await channel.send(text)
    except Exception:
        logger.exception("Không gửi được cảnh báo vào Discord channel %s", ALERT_CHANNEL_ID)


TOKEN_INVALID_ERROR_CODES = (401, 404)  # 401 = token bị revoke/đổi, 404 = bot không còn tồn tại


@tasks.loop(minutes=TOKEN_CHECK_INTERVAL_MINUTES)
async def check_telegram_token() -> None:
    """Call Telegram's getMe periodically; alert only on a CONFIRMED invalid token.

    Distinguishes a real Telegram rejection (401/404 — token actually revoked/rotated)
    from transient network errors (timeout, DNS, connection reset), which are logged
    but don't flip state or alert — those just mean "try again next interval".
    """
    global telegram_token_ok
    try:
        me = await get_me(bot.http_session)
    except TelegramAPIError as exc:
        if exc.error_code not in TOKEN_INVALID_ERROR_CODES:
            logger.warning("getMe trả lỗi không xác định token (bỏ qua lần này): %s", exc)
            return
        if telegram_token_ok:
            telegram_token_ok = False
            logger.error("Telegram token không còn hợp lệ: %s", exc)
            await _alert_admin(
                "🚨 **Token Telegram không còn hợp lệ!**\n"
                "Chủ bot có thể đã đổi token mà chưa kịp báo. Đổi ngay bằng `!thaytoken <token_moi>`, "
                "hoặc cập nhật `TELEGRAM_BOT_TOKEN` trong `.env` trên VPS rồi chạy `sudo systemctl restart panel-bot`."
            )
        return
    except Exception as exc:
        logger.warning("Lỗi mạng khi kiểm tra token Telegram (tạm thời, sẽ thử lại): %s", exc)
        return

    if not telegram_token_ok:
        telegram_token_ok = True
        logger.info("Telegram token đã hợp lệ trở lại (bot: @%s)", me.get("username"))
        await _alert_admin(f"✅ Token Telegram đã hoạt động trở lại (bot: @{me.get('username')}).")


@check_telegram_token.before_loop
async def _before_check_telegram_token() -> None:
    await bot.wait_until_ready()


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


@bot.command(name="thaytoken")
@commands.has_permissions(administrator=True)
async def cmd_thaytoken(ctx: commands.Context, new_token: str = None) -> None:
    """Hot-swap TELEGRAM_BOT_TOKEN: validate first, only apply + persist if it works."""
    global telegram_token_ok

    # Xoá tin nhắn gốc ngay để token không nằm lại trong lịch sử chat.
    try:
        await ctx.message.delete()
    except discord.HTTPException:
        pass

    if not new_token:
        await ctx.send("⚠️ Cú pháp: `!thaytoken <token_moi>`", delete_after=15)
        return

    status_msg = await ctx.send("⏳ Đang kiểm tra token mới...")
    old_token = get_token()
    set_token(new_token)
    try:
        me = await get_me(bot.http_session)
    except Exception as exc:
        set_token(old_token)  # khôi phục token cũ, không lưu token hỏng
        await status_msg.edit(content=f"❌ Token không hợp lệ, đã giữ nguyên token cũ: {exc}")
        return

    set_key(DOTENV_PATH, "TELEGRAM_BOT_TOKEN", new_token)
    telegram_token_ok = True
    logger.info("Đã đổi TELEGRAM_BOT_TOKEN qua !thaytoken (bot: @%s)", me.get("username"))
    await status_msg.edit(
        content=f"✅ Token hợp lệ (bot: @{me.get('username')}) — đã áp dụng ngay và lưu vào `.env`."
    )


@cmd_thaytoken.error
async def cmd_thaytoken_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ Chỉ admin mới dùng được lệnh này.")
    else:
        raise error


def main() -> None:
    bot.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
