# DecoTV + STRM 管理容器

首版把手册中的 11 个 Python 脚本、配置网页和定时运行入口打包进 `strm-manager` 容器。DecoTV、Redis、Jellyfin 保持独立，最终由同一份 Compose 启动四个服务。

## 部署文件与数据目录

```text
compose.yaml             Compose 部署定义（下载到服务器）
compose.build.yaml       可选的本地源码构建配置
.env                     本地环境变量与密码（自行创建，不要提交）
data/
  manager/config.json    脚本配置
  manager/*.json         抓取、线路和统计状态文件
  manager/logs/          定时任务、Telegram 日志
  manager/backups/       配置自动备份
  redis/                 Redis 持久化数据
  jellyfin/              Jellyfin 配置、缓存
  media/movies/          电影 STRM
  media/tv/              剧集 STRM
```

`data/` 不进入镜像，可单独备份。管理容器内的脚本路径仍是 `/opt/m3u`，配置仍是 `/opt/m3u/config.json`；入口脚本把持久化目录链接到该路径，所以现有脚本状态文件布局不变。

## 启动

### Docker（推荐：拉取发布镜像，零构建）

GitHub Actions 会在代码推送到 `main` 后，自动构建并发布两个多架构镜像：

- `ghcr.io/catmaple/strm-manager:latest`：Web 管理应用和现有脚本
- `ghcr.io/catmaple/strm-manager-jellyfin:latest`：Jellyfin 加 FFmpeg/FFprobe 包装脚本

首次发布后，请在 GitHub 仓库的 **Packages** 页面将这两个 Container package 设为 **Public**，这样用户才能匿名拉取镜像。

在已安装 Docker Engine 和 Docker Compose v2 的 Linux 主机上，下载 Compose 文件和环境变量模板：

```sh
mkdir -p strm-manager
cd strm-manager
curl -fsSLo compose.yaml https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
curl -fsSLo compose.build.yaml https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.build.yaml
curl -fsSLo .env.example https://raw.githubusercontent.com/catmaple/strm-manager/main/.env.example
cp .env.example .env
```

编辑 `.env`，替换所有 `CHANGE_ME` 示例值。管理员密码至少 12 位，Session Secret 至少 32 位。然后创建持久化目录，拉取预构建镜像并启动：

```sh
mkdir -p data/manager data/redis data/jellyfin/config data/jellyfin/cache \
  data/media/movies data/media/tv
sudo chown -R 999:999 data/redis
sudo chown -R 1000:1000 data/jellyfin/config data/jellyfin/cache
docker compose pull
docker compose up -d
docker compose ps
```

这是推荐的零构建部署方式。日常用户只需要能够访问 GHCR 拉取镜像，不需要安装 Git 或在服务器构建应用镜像。

上面是常见 Redis/Jellyfin 镜像的容器 UID；如果你使用的镜像版本采用不同 UID，请按该镜像要求调整宿主机目录所有者。STRM 管理容器以 root 身份运行，以便沿用现有媒体目录写入权限，同时已移除 Linux capabilities。

管理网页默认仅绑定在宿主机 `127.0.0.1:8080`，不会直接开放到局域网或公网。远程访问可通过 SSH 隧道：

```sh
ssh -L 8080:127.0.0.1:8080 user@服务器地址
```

然后在本机浏览器打开 <http://127.0.0.1:8080>。使用 `.env` 中的 `STRM_ADMIN_USER` 和 `STRM_ADMIN_PASSWORD` 登录。若要从可信局域网直接访问，可将 `compose.yaml` 中管理服务端口映射左侧的 `127.0.0.1` 改为服务器内网 IP；不要将管理端口直接暴露到公网。

DecoTV 和 Jellyfin 的端口分别是 3000、8096。请按现有方式完成 DecoTV 初始化、配置视频源、创建脚本专用用户，以及 Jellyfin 初始化和媒体库设置。

### 源码构建（可选）

需要自行修改代码或在镜像发布前试用时，在 Linux 主机克隆仓库：

```sh
git clone https://github.com/catmaple/strm-manager.git
cd strm-manager
cp .env.example .env
# 编辑 .env 并创建上文列出的 data 目录
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

源码构建时使用本地工作区的 Dockerfile；构建出的镜像仍使用 Compose 中配置的镜像名。

## 配置网页

登录后可编辑完整 `config.json`。页面提供 JSON 格式化和服务连通状态：

- 保存前校验 JSON、常用字段类型和范围。
- 保存采用临时文件加原子替换；修改前自动备份，保留最近 10 份。
- 密码、Cookie、Token 和 Jellyfin API Key 不会回显。留空保留原值；将值设为 JSON `null` 可清除。
- 配置文件权限为 `0600`。配置页面需要管理员登录，表单有 CSRF 校验。
- 脚本在每次启动时读取配置；保存后下次任务会使用新配置。Telegram Token 或授权 Chat ID 变化后机器人进程会自动重启。

**容器地址要使用 Compose 服务名，而不是 `127.0.0.1`：**

```json
{
  "decotv": {
    "url": "http://decotv:3000"
  },
  "login": {
    "url": "http://decotv:3000/api/login"
  },
  "jellyfin": {
    "url": "http://jellyfin:8096"
  }
}
```

首次启动会生成一份空白默认配置，视频源和凭据需要由你在网页中填入。将原有配置迁移时，建议先备份并检查上面三个服务地址，以及 `list_urls` 中是否仍有旧的本机地址。

迁移现有部署时，建议在首次启动新栈之前复制数据：现有 `/opt/m3u/config.json` 放到 `data/manager/config.json`；`crawl_state.json`、`state.json`、`health.json`、`stats_snapshot.json` 放到 `data/manager/`；Redis 数据复制到 `data/redis/`；Jellyfin 的 `config/`、`cache/` 复制到 `data/jellyfin/config/` 和 `data/jellyfin/cache/`；现有 `/media/movies`、`/media/tv` 内容复制到 `data/media/`。先停掉旧任务再复制，且保留独立备份。复制后再运行 Compose，检查所有目录权限。

## 自动运行

管理容器内置原有计划：

- 每日 02:00：运行 `crawl.py`
- 每日 04:30：运行 `recheck.py`
- 每周日 09:00：运行 `stats.py`

容器时区由 `.env` 的 `TZ` 决定，默认 `Asia/Shanghai`。这些计划任务之间有互斥锁；日志保存在 `data/manager/logs/`。配置 Telegram Token 和非空 `allowed_chat_ids` 后，容器会启动现有 `bot.py`；不配置时不会启动机器人。手动脚本仍可通过容器内命令运行，例如：

```sh
docker compose exec strm-manager python /opt/m3u/crawl.py --types
docker compose exec strm-manager python /opt/m3u/recheck.py --dry --force
docker compose exec strm-manager python /opt/m3u/stats.py --no-send
```

## Jellyfin 包装脚本

Compose 会基于 `jellyfin/jellyfin:latest` 构建一个很薄的自定义镜像，将 `wrapper/ffmpeg` 和 `wrapper/ffprobe` 复制到 Jellyfin 容器的 `/wrapper` 并设置可执行权限。Jellyfin 初始化后，如现有手册所述，把 FFmpeg 路径设置为 `/wrapper/ffmpeg`。这两个包装脚本运行于 Jellyfin 容器，不在管理应用容器中；部署仍只有四个运行容器。

## 更新与备份

GitHub Actions 在 `main` 构建成功后会更新 `latest` 镜像。在服务器部署目录更新 Compose 文件并拉取新镜像：

```sh
curl -fsSLo compose.yaml https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose pull
docker compose up -d
```

升级前备份 `data/` 和 `.env`。`.env` 包含登录凭据，不能公开分享。恢复时停止 Compose，将数据恢复到原目录后再启动。

要固定到某个版本，可在 `.env` 中设置对应镜像 tag，例如：

```dotenv
STRM_MANAGER_IMAGE=ghcr.io/catmaple/strm-manager:v1.0.0
JELLYFIN_IMAGE=ghcr.io/catmaple/strm-manager-jellyfin:v1.0.0
```

推送 `v*` Git tag 时，GitHub Actions 也会发布同名版本镜像。

## 已知范围

- 首版网页管理 `config.json`，没有提供网页手动启动任务；可使用上面的 `docker compose exec` 命令，或现有 Telegram 机器人。
- DecoTV 的 Redis 环境变量和登录 API 路径仍需按实际 DecoTV 镜像版本验证。当前配置默认采用手册中的 `REDIS_URL` 和 `/api/login`。
- 容器化脚本对 DecoTV 地址的必要适配仅使用 `decotv.url`，抓取、STRM 生成和线路选择逻辑沿用现有脚本。
- 在正式迁移前，先在测试环境验证 DecoTV 登录/代理、抓取、Jellyfin 扫描及 VidHub/Infuse/SenPlayer 播放；不要直接覆盖旧数据目录。
