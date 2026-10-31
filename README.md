# 王者体验币自动兑换

本地单账号工具。管理台不需要用户名和密码；游戏登录通过腾讯官方二维码完成，登录态保存在独立的持久化 Chromium 配置中。

目标页面：<https://pvp.qq.com/cp/a20161115tyf/page2.shtml>

## 功能

- 管理台免登录，直接打开即可使用
- QQ / 微信扫码登录，自动保存 Cookie
- 可保存 QQ 账号密码，登录失效时自动尝试密码登录
- QQ 账号和密码使用本地密钥加密后写入 SQLite
- 密码登录遇到手机验证码或图片验证码时，可发送/重新发送验证码并手动提交
- 可填写账号备注，区分不同游戏账号
- 每日指定时间自动兑换一次
- 六种奖励可多选，按“本地日期 + 奖励”分别去重
- PushPlus 和 OneBot v11 推送
- 推送兑换成功、兑换失败和登录过期
- 执行截图、历史记录和错误详情
- 登录态每 10 分钟检查一次，过期只提醒一次

## 本地启动

需要 Python 3.12 或更高版本。

```powershell
python3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

也可以直接运行：

```powershell
.\start.ps1
```

打开 <http://127.0.0.1:8765>。

## 首次使用

1. 填写账号备注，点击“扫码登录”。
2. 使用手机 QQ 或微信扫描弹窗内的官方二维码。
3. 可选：填写 QQ 账号和密码，开启“自动登录”，然后保存配置。
4. 如果密码登录要求 2FA，可点击“发送验证码”，再在左侧输入手机或图片验证码并提交。
5. 登录成功后，确认目标页显示“正式服账号绑定”状态。
6. 如果尚未绑定，点击“完整窗口”，在弹出的 Chromium 中完成正式服角色选择。
7. 一项或多项选择奖励，并设置每日执行时间。
8. 填写 PushPlus Token 或 OneBot 配置，先发送测试推送。
9. 点击“保存配置”。

“完整窗口”只适用于能显示桌面窗口的 Windows / macOS 本机运行环境。Docker 或远程无桌面服务器只使用扫码登录。

## 执行规则

- 服务每 20 秒检查一次计划时间。
- 到达计划时间后，只要当天还没有兑换记录，就会执行一次。
- 多项奖励会按选择顺序依次执行，每项独立记录成功或失败。
- 某个奖励当天成功后，同日不会再次提交该项。
- 当天失败后可由用户手动重试，自动任务不会在同一天重复请求。
- 目标页本身限制“每个礼包每天只能兑换一次”，本项目同时做本地去重。
- 登录过期时，自动任务不会尝试绕过登录；会推送提醒并等待重新扫码。
- 保存了 QQ 账号密码并启用自动登录时，失效后会自动填写腾讯官方密码表单。
- 密码错误、图片验证码、设备锁、短信验证或风控验证不会被绕过，会推送“自动登录失败”并等待人工处理。

## PushPlus

在 [PushPlus](https://www.pushplus.plus/) 获取 Token，存入管理台。

推送内容包括：

- 兑换成功：账号、奖励、获得内容、时间
- 兑换失败：账号、奖励、失败原因、时间
- 登录过期：账号、失效时间、重新登录提示

Token 使用 `data/secret.key` 加密后写入 SQLite。

QQ 账号和密码同样使用 `data/secret.key` 加密后写入 SQLite，管理台 API 只返回脱敏账号和“是否已保存”状态。密码不会写入日志、推送内容或执行截图。

## OneBot v11

填写 OneBot HTTP API 根地址，例如：

```text
http://127.0.0.1:3000
```

程序调用 `/send_msg`，支持：

- 私聊：`message_type=private`，使用 QQ 号
- 群聊：`message_type=group`，使用群号
- Access Token：可选，使用 `Authorization: Bearer <token>`

## Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100
```

默认绑定所有网卡的 `0.0.0.0:8675`，可在 `.env` 中修改端口或绑定地址：

```text
PVP_BIND=0.0.0.0
PVP_PORT=8675
```

访问 `http://服务器地址:8675`。管理台没有登录鉴权，直接公开到公网前必须增加反向代理认证和 HTTPS。

Docker 中登录二维码由无头 Chromium 截图提供，扫码后 Cookie 会保存在数据卷内。2FA 验证码可在网页中发送和输入；完整浏览器窗口不可用。

Compose 使用命名卷 `pvp-reward-data` 持久化整个 `/app/data`，包括 SQLite 数据库、Cookie、浏览器配置、加密密钥和执行截图。重建容器不会丢失数据：

```bash
docker compose down
docker compose up -d --build
```

只有显式执行 `docker compose down -v` 才会删除数据卷，请勿在正式环境使用。

查看健康状态：

```bash
docker compose ps
docker inspect --format '{{json .State.Health}}' pvp-reward-pvp-reward-1
```

备份数据卷：

```bash
mkdir -p backups
docker run --rm \
  -v pvp-reward-data:/data:ro \
  -v "$PWD/backups:/backup" \
  alpine sh -c 'tar czf /backup/pvp-reward-$(date +%Y%m%d-%H%M%S).tar.gz -C /data .'
```

恢复前先停止服务并确认备份文件，然后再覆盖数据卷。不要把 `data/secret.key` 与数据库分开备份，否则保存的推送 Token 将无法解密。

## 数据

```text
data/pvp-reward.db          配置、推送 Token、执行记录
data/secret.key             推送 Token 加密密钥
data/browser-profile/       游戏登录 Cookie 和浏览器持久化数据
data/artifacts/             执行截图
```

备份时应同时备份整个 `data/` 目录。删除 `browser-profile/` 会要求重新登录；丢失 `secret.key` 会导致已保存的推送 Token 无法解密。

浏览器 Cookie 由 Chromium 自身管理，不能视为加密保险箱。请限制数据目录访问权限。

## 安全边界

- Docker 默认监听 `0.0.0.0:8675`，无登录管理台会暴露在服务器所有网卡上。
- 如需远程访问，请在前面加 HTTPS 和反向代理认证，并限制来源 IP。
- 本项目不绕过验证码、风控或目标站点限制。
- 本项目会在启用 QQ 自动登录时加密保存账号密码；仅用于填写腾讯官方登录表单，不绕过验证码、设备锁、短信验证或风控。
- 2FA 验证码不会写入数据库或日志；程序只会在当前浏览器会话中填写你手动输入的验证码。
- 目标页面或活动规则变化时，兑换按钮、登录入口或回调结构可能需要同步调整。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app\static\app.js
```

测试使用模拟浏览器和模拟推送接口，不会执行真实兑换，也不需要游戏账号。

运行完整发布流程：

```powershell
.\scripts\publish-and-stop.ps1 -Message "chore: update"
```

脚本会先运行测试和 JavaScript 校验，再提交并同步到 GitHub，只有同步成功后才停止本机 `8765` 端口的应用。测试或推送失败时会保留本地应用。

## 目录

```text
app/
  main.py                 FastAPI 路由
  database.py             SQLite 表结构
  rewards.py              奖励目录
  schemas.py              请求校验
  security.py             推送密钥加密
  services/
    browser.py            持久化 Chromium、登录和兑换执行
    runner.py             去重、日志和结果通知
    scheduler.py          每日调度和登录态监控
    push.py               PushPlus / OneBot
    settings.py           设置读写
  static/                 管理台
tests/                    API、调度和推送测试
```

## 常见问题

**点击扫码登录后没有二维码**

确认服务器能访问 `pvp.qq.com` 和腾讯登录域名，然后重试。目标页首次加载通常需要几秒。

**登录成功但提示需要绑定**

点击“完整窗口”，在 Chromium 中完成正式服角色选择。完成后再点“检查登录状态”。

**Windows 提示找不到时区**

安装依赖时会自动安装 `tzdata`。若使用已有虚拟环境，请重新执行 `pip install -r requirements.txt`。

**Docker 内存不足**

Chromium 需要额外内存，建议至少 1 GB 可用内存，并保留 Compose 中的 `shm_size: "1gb"`。

