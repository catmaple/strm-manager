# DecoTV + STRM 管理容器

[English](README.en.md) | 简体中文 | [MIT 许可证](LICENSE)

本项目将 DecoTV、Jellyfin 与 STRM 管理工具整合为一套 Docker Compose 服务：通过 DecoTV 和配置的视频源获取播放链接，生成 STRM 文件供 Jellyfin 建库，从而支持在 Jellyfin 网页端及兼容的客户端中浏览和播放在线视频。

项目只在本地保存配置、媒体信息和 `.strm` 播放链接文件，不下载或保存视频文件本身。实际播放内容由 DecoTV 或用户配置的视频源提供，播放可用性取决于相应服务和来源。

**本项目不提供、托管或分发任何视频内容，也不包含视频源。** 用户需自行配置并确保其对所使用的视频源和内容具有合法访问权限。

本项目将现有 STRM Python 脚本、配置网页和定时任务封装在 `strm-manager` 容器中，并通过 Docker Compose 一次启动四个服务：

| 服务 | 用途 | 地址/端口 |
|---|---|---|
| DecoTV | 浏览视频源、提供源请求代理 | `http://服务器IP:3000` |
| Redis | DecoTV 数据持久化 | 仅 Compose 内部访问 |
| Jellyfin | 媒体库及客户端播放服务 | `http://服务器IP:8096` |
| STRM Manager | 配置网页、脚本和定时任务 | 默认监听 `0.0.0.0:8080`，可通过服务器 IP 访问 |

配置、Redis 数据和 Jellyfin 数据保存在部署目录的 `data/` 中，不打包进镜像。STRM 媒体文件不保存在项目目录 `data/media/` 中；容器脚本仍使用容器内路径 `/media/movies` 和 `/media/tv`，并分别映射到宿主机 `/media/movies` 和 `/media/shows`。这样可避免镜像更新时丢失媒体文件，并与原始 STRM 目录布局兼容。

## 1. 准备 Linux 主机

以下示例适用于 Debian 12 / Ubuntu 主机。建议至少 2 核 CPU、4 GB 内存，并为 Jellyfin 元数据和缓存预留足够磁盘空间。

### 系统基础软件

```bash
apt update && apt upgrade -y
apt install -y curl ca-certificates openssl

# Docker Engine、Buildx 和 Docker Compose plugin
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

docker version
docker compose version
```

本部署不需要在宿主机安装 Python、cron 或 Redis：脚本、Python 运行环境和定时任务在 STRM Manager 容器中，Redis 也由 Compose 启动。

> 如果 `apt` 提示 locale 警告，通常可忽略；需要配置时可安装 `locales` 后运行 `dpkg-reconfigure locales`。
>
> 国内网络拉取 GHCR、Docker Hub 或 DecoTV 镜像较慢时，请为 Docker 配置可用的网络代理或镜像加速。

## 2. 下载部署文件并设置密码

创建独立部署目录，之后的 Compose、配置和数据都放在此目录中：

```bash
mkdir -p /opt/strm-manager
cd /opt/strm-manager

curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
curl -fsSLo .env.example \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/.env.example
cp .env.example .env
chmod 600 .env
```

编辑环境变量：

```bash
nano .env
```

可用 OpenSSL 生成随机密码和会话密钥：

```bash
printf 'STRM_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 16)"
printf 'STRM_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)"
printf 'DECOTV_PASSWORD=%s\n' "$(openssl rand -hex 16)"
```

将生成的值分别填入 `.env` 对应变量。管理员密码和 DecoTV 密码不要共用。

必须替换所有 `CHANGE_ME` 示例值。变量说明：

| 变量 | 用途 |
|---|---|
| `STRM_ADMIN_USER` | 管理网页登录用户名 |
| `STRM_ADMIN_PASSWORD` | 管理网页登录密码，至少 12 个字符 |
| `STRM_SESSION_SECRET` | 网页会话签名密钥，至少 32 个字符 |
| `STRM_PORT` | 管理网页宿主机端口，默认 8080 |
| `STRM_HOST_BIND` | 管理网页监听地址；默认 `0.0.0.0`，可改为 `127.0.0.1` 只允许本机访问 |
| `TZ` | 容器时区，默认 `Asia/Shanghai` |
| `DECOTV_USERNAME` / `DECOTV_PASSWORD` | DecoTV 管理账号 |
| `STRM_MANAGER_IMAGE` / `JELLYFIN_IMAGE` | 镜像名，默认使用 GHCR 的 `latest` 发布镜像 |

不要把 `.env` 上传到公开仓库或发送给他人。密码尽量避免使用未处理的 `$` 字符，因为 Compose 会对环境变量中的 `$` 做插值。

## 3. 创建持久化目录并检查权限

```bash
mkdir -p \
  data/manager \
  data/redis \
  data/jellyfin/config \
  data/jellyfin/cache \
  /media/movies \
  /media/shows

# 常见 Redis 和 Jellyfin 镜像的运行 UID
chown -R 999:999 data/redis
chown -R 1000:1000 data/jellyfin/config data/jellyfin/cache
```

说明：

- `data/` 仅存放应用配置、日志、Redis 数据和 Jellyfin 数据。
- `STRM` 生成的 `.strm` 文件不放在项目目录里，而放在宿主机 `/media/movies` 和 `/media/shows`。
- 容器内脚本仍按原始设计使用 `/media/movies` 和 `/media/tv`，Compose 会把它们映射到宿主机对应目录。

如果所用镜像版本的运行 UID 不同，请按镜像文档调整对应目录所有者。`/media/movies` 和 `/media/shows` 必须允许 STRM Manager 写入，并允许 Jellyfin 读取。当前管理容器以 root 身份运行，同时移除了 Linux capabilities，以兼容既有 STRM 写入目录权限。

目录用途：

```text
data/
  manager/
    config.json       网页管理的配置文件（首次启动自动生成）
    crawl_state.json  批量抓取进度
    health.json       线路复测状态
    logs/             定时任务和 Telegram 日志
    backups/          自动保存的配置备份
  redis/              Redis 持久化数据
  jellyfin/
    config/           Jellyfin 用户、媒体库及元数据
    cache/            Jellyfin 缓存

/media/
  movies/             电影 STRM（宿主机目录）
  shows/              剧集 STRM（宿主机目录）
```

## 4. 拉取镜像并启动四个容器

推荐使用发布镜像，**无需克隆源码或在服务器构建应用镜像**：

```bash
cd /opt/strm-manager
docker compose config -q
docker compose pull
docker compose up -d
docker compose ps
```

使用的镜像：

- `ghcr.io/decohererk/decotv:latest`
- `redis:7-alpine`
- `ghcr.io/catmaple/strm-manager-jellyfin:latest`
- `ghcr.io/catmaple/strm-manager:latest`

DecoTV 和 Redis 使用上游发布镜像。Jellyfin 包装镜像基于官方 Jellyfin 镜像，并加入本项目需要的 FFmpeg/FFprobe 包装脚本。GitHub Actions 在代码推送到 `main` 或创建 `v*` 版本 tag 后，构建并发布 STRM Manager 和 Jellyfin 包装镜像；首次发布成功后，需要在 GitHub 仓库的 **Packages** 页面将两个包设为 **Public**，用户才可以匿名拉取。

Compose service definitions and volume mappings are maintained in [`compose.yaml`](compose.yaml). Use that file as-is for normal deployment; avoid copying only part of its service definitions into another Compose file.

## 5. 查看服务状态和启动日志

```bash
cd /opt/strm-manager
docker compose ps

docker compose logs --tail 50 decotv
docker compose logs --tail 50 decotv-redis
docker compose logs --tail 50 jellyfin
docker compose logs --tail 100 strm-manager
```

确认四个容器均为 `Up`。Redis 健康检查通过时显示 `healthy`。管理应用的健康检查通过时显示 `healthy`。

检查 DecoTV、Jellyfin、Redis 端口和包装脚本：

```bash
curl -I --max-time 10 http://127.0.0.1:3000/
curl -i --max-time 10 http://127.0.0.1:8096/health
docker exec decotv-redis redis-cli ping
docker exec jellyfin ls -l \
  /usr/lib/jellyfin-ffmpeg/ffmpeg \
  /usr/lib/jellyfin-ffmpeg/ffmpeg.real \
  /usr/lib/jellyfin-ffmpeg/ffprobe \
  /usr/lib/jellyfin-ffmpeg/ffprobe.real
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffmpeg -version
```

预期 Redis 返回 `PONG`，Jellyfin 健康检查返回 HTTP 成功状态。`ffmpeg` 和 `ffprobe` 是包装脚本，原版程序分别保留为 `.real` 文件；包装脚本应可执行，`ffmpeg -version` 应能正常输出版本。若 DecoTV 启动日志出现 Redis 连接错误，查看本 README 的“已知问题”部分，并核实当前 DecoTV 版本使用的 Redis 环境变量。

## 6. 完成 DecoTV 和 Jellyfin 初始化

### DecoTV

浏览器打开 `http://服务器IP:3000/login`，使用 `.env` 中的 `DECOTV_USERNAME` 和 `DECOTV_PASSWORD` 登录。

1. 在管理页面配置可用的视频源。
2. 创建供脚本使用的普通用户（例如 `script`），不要将管理员账号密码填入脚本配置。
3. 在 DecoTV 管理页验证视频源、搜索和播放功能。
4. 如果使用 Redis 存储，检查 `/admin` 页面不再提示本地存储模式，并检查数据目录：

   ```bash
   ls -la data/redis
   ```

### Jellyfin

浏览器打开 `http://服务器IP:8096`，完成首次设置：

1. 创建 Jellyfin 管理员用户。
2. 创建“电影”库，目录设为 `/media/movies`。
3. 创建“节目/Shows”库，目录设为 `/media/tv`。该容器内路径映射到宿主机的 `/media/shows`。
4. 可关闭章节图片提取和 Trickplay/预览图生成，避免 Jellyfin 对远程 STRM 流进行大量探测。
5. 在 Dashboard → Advanced → API Keys 创建 API Key，稍后填入管理网页的 `jellyfin.api_key`。
6. Jellyfin 的 FFmpeg path 固定为 `/usr/lib/jellyfin-ffmpeg/ffmpeg`，无需也无法在网页中改成 `/wrapper/ffmpeg`。项目构建的 Jellyfin 镜像会将包装脚本安装到这个固定路径，并把原版 FFmpeg 保留为 `/usr/lib/jellyfin-ffmpeg/ffmpeg.real`。

如需网页端播放带特殊 HLS 切片后缀的流，可用真实 m3u8 地址测试已安装到 Jellyfin 固定路径的包装器：

```bash
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
  -show_entries stream=codec_type,codec_name -of compact \
  -i "https://替换为可用播放地址/index.m3u8"
```

## 7. 打开 STRM Manager 网页并配置脚本

管理网页默认绑定到 `0.0.0.0:${STRM_PORT:-8080}`，可直接通过服务器 IP 访问；若只想本机访问，可在 `.env` 中设置：

```bash
STRM_HOST_BIND=127.0.0.1
```

在本机浏览器打开 <http://127.0.0.1:8080>，用 `.env` 中的 `STRM_ADMIN_USER`、`STRM_ADMIN_PASSWORD` 登录。

管理控制台和登录页支持简体中文、English。页面顶部的语言选择会保存在当前浏览器中；切换语言不会更改 `config.json`。

若要通过 SSH 隧道访问，只需本机端口转发：

```bash
ssh -L 8080:127.0.0.1:8080 用户名@服务器IP
```

如需显式指定只监听某个网卡地址，也可以在 `.env` 中写成：

```bash
STRM_HOST_BIND=192.168.1.110
```

然后应用配置：

```bash
docker compose up -d strm-manager
```

不要把管理网页、DecoTV 或 Jellyfin 管理端口直接暴露到公网；远程使用请通过 VPN、SSH 隧道或配置妥当的 HTTPS 反向代理。

管理页面中的 `config.json` 按现有格式保存。Docker Compose 网络内要使用服务名，而不是 `127.0.0.1`：

```json
{
  "decotv": {
    "url": "http://decotv:3000"
  },
  "login": {
    "url": "http://decotv:3000/api/login",
    "username": "script",
    "password": "填入 DecoTV 脚本专用用户密码"
  },
  "jellyfin": {
    "url": "http://jellyfin:8096",
    "api_key": "填入 Jellyfin API Key"
  },
  "cms_apis": [
    "https://你的CMS接口/api.php/provide/vod"
  ],
  "crawl": {
    "via_decotv": true,
    "sources": []
  }
}
```

表单支持选择/添加 CMS API、预览分类、配置定时任务、测试连接、Dry Run，以及手动运行 DecoTV 默认分类扫描、视频源扫描、线路复测和统计。点击“校验并保存”才会保存配置；测试连接、Dry Run 和立即运行操作不会保存本次表单修改。配置保存时会自动备份，最多保留最近 10 份。密码、Telegram Token 和 Jellyfin API Key 会脱敏；密钥输入框留空表示保留原值。完整 `config.json` 查看窗口也会隐藏敏感值。

网页表单隐藏了部分高级字段（例如 `list_urls`）；这些字段会从当前配置保留，不会因表单保存而被删除。若需修改隐藏字段或清除已保存的密钥，请先备份，再直接编辑 `data/manager/config.json`。

测试配置与源访问：

```bash
cd /opt/strm-manager
docker compose exec strm-manager python -m json.tool /data/config.json
docker compose exec strm-manager python /opt/m3u/crawl.py --types
```

`crawl.py --types` 应列出已配置源的分类。没有配置 `crawl.sources` 或源接口无法访问时，先回管理网页修正配置，不要开始大批量抓取。

重点：容器内脚本仍然使用 `/media/movies` 和 `/media/tv`，但它们分别映射到宿主机的 `/media/movies` 和 `/media/shows`。因此最终生成的 STRM 永远在宿主机 `/media/movies` 和 `/media/shows`，而不是项目目录 `data/media/`；Jellyfin 媒体库仍填写容器内路径 `/media/movies` 和 `/media/tv`。

## 8. 验证 STRM 入库与播放

先小批量测试，避免首次就触发大量远程探测：

```bash
docker compose exec strm-manager python /opt/m3u/crawl.py --dry --new 5 --pages 5
```

确认预览内容和电影/剧集分类符合预期后，可按实际源分类 ID 执行少量真实抓取，例如：

```bash
docker compose exec strm-manager python /opt/m3u/crawl.py --new 5 --pages 5 --type 6
```

检查 STRM 文件：

```bash
find /media/movies /media/shows -name '*.strm' -type f | head
```

然后在 Jellyfin 管理页扫描媒体库，确认条目出现，并分别用 Jellyfin 网页端和目标客户端（VidHub、Infuse、SenPlayer）实际测试播放。必要时可执行：

```bash
docker compose exec strm-manager python /opt/m3u/add.py "片名"
docker compose exec strm-manager python /opt/m3u/recheck.py --dry --force
docker compose exec strm-manager python /opt/m3u/stats.py --no-send
```

## 9. 定时任务、手动运行和日志

可在控制台的“定时任务”区域启用/禁用总开关和单项任务、设置运行时间，并选择批量扫描使用 `auto.py`（DecoTV 默认分类）或 `crawl.py`（已配置的视频源分类）。默认计划时间按 `.env` 中的 `TZ` 时区计算：

- 每日 02:00：批量扫描（默认 `auto.py`；也可在控制台选择 `crawl.py`）
- 每日 04:30：`recheck.py` 线路复测
- 每周日 09:00：`stats.py` 周统计

默认计划可在网页表单中修改。配置保存后会在下一次调度时使用。页面上的“扫描DecoTV默认分类”“扫描视频源分类”“线路复测”“统计数量”按钮可分别手动启动对应任务。定时批量扫描（`auto.py` 或 `crawl.py`）成功结束后，会自动执行电影分类整理；网页不提供单独的整理按钮。整理会扫描 TV 目录中只有一季一集的候选，并通过已配置 CMS 的分类名称、片名/年份和 STRM 链接精确匹配确认电影后，自动移动到 Movies 目录。无法唯一确认、包含多集或目标文件冲突的内容会保留并记录日志，不会自动猜测或删除。

电影误入 TV 目录的常见原因是 CMS 分类被标记为“剧集”，或同一电影的多个播放线路被当作多集。抓取逻辑现会优先参考 CMS 条目类型和明确的季号；若分类信息缺失或含糊，仍会使用配置中的类型作为回退。建议同时检查 `crawl.sources` 的类别映射。

任务互斥，定时任务和手动任务日志保存在 `data/manager/logs/`。查看容器输出和持久化日志：

```bash
docker compose logs -f strm-manager
tail -n 100 data/manager/logs/scheduler.log
tail -n 100 data/manager/logs/crawl.log
tail -n 100 data/manager/logs/recheck.log
```

如需使用 Telegram 机器人，在网页配置 `telegram.token` 和非空的 `telegram.allowed_chat_ids`。机器人只响应授权的 Chat ID；更改 Token 或授权列表后，管理容器会自动重启机器人进程。

## 10. 迁移现有部署

迁移前先停止旧版 cron/systemd 任务和机器人，避免新旧环境同时写入状态文件；保留一份完整备份。将数据复制到新部署目录：

| 原路径 | 新路径 |
|---|---|
| `/opt/m3u/config.json` | `data/manager/config.json` |
| `/opt/m3u/crawl_state.json`、`state.json`、`health.json`、`stats_snapshot.json` | `data/manager/` |
| DecoTV Redis 数据目录 | `data/redis/` |
| Jellyfin `config/` | `data/jellyfin/config/` |
| Jellyfin `cache/` | `data/jellyfin/cache/` |
| 电影 STRM 文件 | `/media/movies/` |
| 剧集 STRM 文件 | `/media/shows/` |

如果当前 STRM 文件还在部署目录的 `data/media/` 下，先停止抓取任务并把已有文件复制到新位置。旧剧集目录可能叫 `tv` 或 `shows`，按实际存在的目录复制：

```bash
mkdir -p /media/movies /media/shows
[ ! -d /opt/strm-manager/data/media/movies ] || \
  cp -a /opt/strm-manager/data/media/movies/. /media/movies/
if [ -d /opt/strm-manager/data/media/tv ]; then
  cp -a /opt/strm-manager/data/media/tv/. /media/shows/
elif [ -d /opt/strm-manager/data/media/shows ]; then
  cp -a /opt/strm-manager/data/media/shows/. /media/shows/
fi
```

复制后更新 Compose 文件并重建两个使用媒体目录的容器：

```bash
cd /opt/strm-manager
curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose config -q
docker compose up -d --force-recreate jellyfin strm-manager
```

确认文件已复制且目录权限正确后，再检查 `/media/movies`、`/media/shows` 中的 STRM 文件，并在 Jellyfin 中扫描媒体库。迁移的 `config.json` 中，DecoTV 和 Jellyfin 地址需使用 `http://decotv:3000` 与 `http://jellyfin:8096`。迁移期间不要删除旧目录或备份；确认新环境完成抓取、扫描和客户端播放后，再清理旧数据。

## 11. 更新、版本固定与源码构建

发布新代码后，更新 Compose 并拉取新镜像：

```bash
cd /opt/strm-manager
curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose pull
docker compose up -d
docker compose ps
```

要固定版本，在 `.env` 中指定相同版本的管理应用和 Jellyfin 包装镜像：

```dotenv
STRM_MANAGER_IMAGE=ghcr.io/catmaple/strm-manager:v1.0.0
JELLYFIN_IMAGE=ghcr.io/catmaple/strm-manager-jellyfin:v1.0.0
```

推送 `v*` Git tag 后，GitHub Actions 会发布相同名称的镜像 tag。升级前备份 `data/` 和 `.env`；`.env` 含有登录凭据，不要公开分享。

需要自行修改代码时才需要克隆仓库并本地构建：

```bash
git clone https://github.com/catmaple/strm-manager.git
cd strm-manager
cp .env.example .env
# 编辑 .env、创建 data 持久化目录
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

## 12. 故障排查与已知限制

| 现象 | 检查方式 |
|---|---|
| 管理页面打不开 | `docker compose ps`、`docker compose logs --tail 100 strm-manager`；确认 `.env` 管理密码不少于 12 位、Session Secret 不少于 32 位 |
| DecoTV 一直显示本地存储或日志 Redis 连接失败 | `docker compose logs --tail 100 decotv decotv-redis`；手册中使用的 `REDIS_URL` 需按当前 DecoTV 版本确认 |
| 源分类查询失败 | 检查 `config.json` 的 CMS 地址、`crawl.sources` 和 `via_decotv`；确认 DecoTV 登录 API 路径和请求字段适用于当前版本 |
| STRM 已生成但 Jellyfin 无内容 | 检查宿主机 `/media/movies`、`/media/shows` 是否有文件；Jellyfin 容器内媒体库路径应为 `/media/movies`、`/media/tv`，再触发库扫描 |
| HLS 剧集无法播放 | 确认 Jellyfin FFmpeg path 是 `/usr/lib/jellyfin-ffmpeg/ffmpeg`，并用真实 m3u8 地址测试 `/usr/lib/jellyfin-ffmpeg/ffprobe`；查看 FFmpeg 转码日志确认具体失败原因 |
| GHCR 拉取失败 | 检查镜像是否已由 GitHub Actions 发布，以及 GitHub Packages 是否设为 Public；私有包需要先 `docker login ghcr.io` |
| `redis:7-alpine` 报 `dial tcp [IPv6地址]:443: connect: network is unreachable` | Docker Hub 解析到了 IPv6 地址，但服务器没有可用 IPv6 出口；按下方步骤检查 IPv4/IPv6 网络，并为 Docker daemon 配置网络代理或可用的 registry mirror |

### Docker Hub IPv6 网络错误

此错误发生在 Docker Hub 网络连接阶段，不是 Redis 配置或 Compose 文件错误。`docker compose pull` 失败时，后续 `docker compose up -d` 也无法创建完整服务；先修复镜像拉取并确认成功，再启动容器。

先在宿主机上检查 IPv4、IPv6 连通性：

```bash
# IPv4 正常时通常返回 HTTP/1.1 401 Unauthorized；这是 registry 的正常匿名响应
curl -4 -I --connect-timeout 10 https://registry-1.docker.io/v2/

# 若服务器没有 IPv6 出口，这条命令会失败；同时检查 IPv6 路由
curl -6 -I --connect-timeout 10 https://registry-1.docker.io/v2/
ip -6 route
```

- **IPv4 和 IPv6 都失败：**服务器当前无法连接 Docker Hub。为 Docker daemon 配置能访问 Docker Hub 的代理，或配置你所使用网络提供商的可信 Docker Hub registry mirror。只在当前 Shell 设置 `HTTP_PROXY`/`HTTPS_PROXY` 通常不会传递给后台 Docker daemon。
- **IPv4 成功、IPv6 失败：**优先修复服务器 IPv6 默认路由；若该网络不提供 IPv6，禁用无效的 IPv6 配置或让 Docker daemon 通过可用代理/镜像站访问 Docker Hub。不要把临时解析出的某个 CDN IP 固定写入 `/etc/hosts`。

Docker daemon 使用 systemd 代理的配置示例（把地址替换为自己的代理；不要原样照抄占位地址）：

```bash
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://代理地址:端口"
Environment="HTTPS_PROXY=http://代理地址:端口"
Environment="NO_PROXY=localhost,127.0.0.1,.local"
EOF

systemctl daemon-reload
systemctl restart docker
docker info | grep -i proxy
```

重启 Docker daemon 会影响当前运行的容器；请在维护窗口操作。配置好代理或 registry mirror 后重新拉取并确认四个服务镜像均成功：

```bash
cd /opt/strm-manager
docker compose pull
docker compose up -d
docker compose ps
```

- STRM 文件只是播放链接；播放记录和继续播放进度由 Jellyfin 与客户端管理，不写入 `.strm` 文件。
- 网页保存配置时会保留未显示的现有字段；查看完整配置时敏感值会脱敏。
- DecoTV Redis 配置变量、登录 API 路径和请求字段应按实际部署版本验证。
- 正式迁移前，先在测试环境验证 DecoTV 登录/代理、抓取、Jellyfin 扫描及目标客户端播放。
