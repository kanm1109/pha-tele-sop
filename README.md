# Discord -> Telegram Panel Bot

Bảng điều khiển Discord (5 nút bấm) gửi thông báo trực tiếp sang Telegram qua Bot API, không cần đăng nhập tài khoản cá nhân.

## Cấu trúc

- `config.py` - đọc và validate biến môi trường (`.env`)
- `telegram_service.py` - client async gọi `POST /bot<TOKEN>/sendMessage`, dùng chung một `aiohttp.ClientSession`
- `bot.py` - bot Discord (`PanelBot`), `ControlPanelView` (persistent) và lệnh `/dieukhien` / `!dieukhien`
- `requirements.txt`, `.env.example`

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # rồi điền token thật vào .env
```

## Cấu hình Discord Developer Portal

1. Vào https://discord.com/developers/applications -> tạo/chọn ứng dụng -> tab **Bot**.
2. Lấy **Token**, dán vào `DISCORD_BOT_TOKEN` trong `.env`.
3. Bật **Privileged Gateway Intents** -> **Message Content Intent** (bot dùng lệnh prefix `!dieukhien` và intents mặc định).
4. Tab **OAuth2 -> URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Use Slash Commands` (Read Messages/View Channels đi kèm mặc định)
5. Dùng URL sinh ra để mời bot vào server.

## Cấu hình Telegram

1. Tạo bot qua [@BotFather](https://t.me/BotFather), lấy token -> `TELEGRAM_BOT_TOKEN`.
2. Thêm bot vào nhóm Telegram mục tiêu, cấp quyền gửi tin nhắn.
3. Lấy `chat_id` của nhóm (số âm với nhóm/supergroup, ví dụ `-1002682825542`) -> `TELEGRAM_CHAT_ID`.

## Chạy bot

```bash
python bot.py
```

Gõ `/dieukhien` (slash command, cần chờ vài giây để Discord sync) hoặc `!dieukhien` trong kênh để hiển thị bảng điều khiển. Nhấn nút bất kỳ sẽ gửi thông báo tương ứng sang nhóm Telegram và phản hồi ẩn (ephemeral) trạng thái thành công/lỗi ngay trên Discord.

### Lệnh `!spam` / `!stopspam` (chỉ admin)

- `!spam`: gửi lặp lại tin "Kiểm tra kết nối." sang Telegram mỗi `SPAM_INTERVAL_MS` (mặc định 5000ms = 5 giây), chạy nền bất đồng bộ (không chặn bot), tự dừng sau tối đa `SPAM_MAX_ITERATIONS` lần (mặc định 720 lần, ~1 giờ) nếu quên tắt.
- `!stopspam`: dừng ngay tiến trình `!spam` đang chạy.
- Cả 2 lệnh yêu cầu quyền **Administrator** trên server Discord — người không có quyền sẽ nhận phản hồi từ chối.
- Chỉnh `SPAM_INTERVAL_MS`/`SPAM_MAX_ITERATIONS` trực tiếp trong `bot.py` nếu cần đổi tần suất/giới hạn.

## Deploy lên VPS (Ubuntu/Debian, dùng systemd)

```bash
# 1. Cài Python nếu VPS chưa có
sudo apt update && sudo apt install -y python3 python3-venv git

# 2. Copy project lên VPS (scp/rsync/git clone), ví dụ đặt tại /opt/panel-bot
sudo mkdir -p /opt/panel-bot
# scp -r ./* user@vps:/opt/panel-bot/   (chạy từ máy local)

# 3. Tạo venv và cài thư viện trên VPS
cd /opt/panel-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 4. Tạo file .env thật trên VPS (đừng copy qua git/scp công khai, gõ tay hoặc scp riêng qua kênh an toàn)
nano .env    # dán DISCORD_BOT_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 5. Cài đặt service systemd (file mẫu: deploy/panel-bot.service)
sudo cp deploy/panel-bot.service /etc/systemd/system/panel-bot.service
sudo useradd -r -s /usr/sbin/nologin botuser   # user chạy service, nếu chưa có
sudo chown -R botuser:botuser /opt/panel-bot
sudo systemctl daemon-reload
sudo systemctl enable --now panel-bot
```

Kiểm tra & quản lý:

```bash
sudo systemctl status panel-bot     # xem trạng thái
sudo journalctl -u panel-bot -f     # xem log realtime
sudo systemctl restart panel-bot    # restart sau khi sửa code/.env
```

Vì `PanelBot.setup_hook()` tự đăng ký lại `ControlPanelView` mỗi lần khởi động, các nút trên tin nhắn panel cũ trong Discord vẫn hoạt động bình thường sau khi restart service (không cần gửi lại `/dieukhien`).

- **Windows** (thay thế nếu không dùng VPS Linux): dùng Task Scheduler hoặc `nssm`/`pm2` để chạy `bot.py` như service.
- **Docker** (tuỳ chọn): build image từ `requirements.txt`, truyền `.env` qua `--env-file`, không bake token vào image.
- Luôn đảm bảo `.env` không được commit (đã có trong `.gitignore`); chỉ commit `.env.example` với giá trị placeholder.
