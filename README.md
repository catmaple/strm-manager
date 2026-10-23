# DecoTV + STRM 管理容器

首版把手册中的 11 个 Python 脚本、配置网页和定时运行入口打包进 `strm-manager` 容器。DecoTV、Redis、Jellyfin 保持独立，最终由同一份 Compose 启动四个服务。

## 部署文件与数据目录

```text
compose.yaml             Compose 部署定义（下载到服务器）
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

管理应用和 Jellyfin 包装镜像的构建上下文直接使用 GitHub 仓库 `https://github.com/catmaple/strm-manager.git#main`。服务器只需要保存 Compose 文件、`.env` 和持久化数据；执行构建时 Docker 会从 GitHub 获取仓库代码，不需要在服务器手动克隆源码。

## 启动

在已安装 GitHub 可访问网络、Docker Engine、Docker Buildx 和 Docker Compose v2 的 Linux 主机上，创建部署目录并下载部署文件：

```sh
mkdir -p strm-manager
cd strm-manager
curl -fsSLo compose.yaml https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
curl -fsSLo .env.example https://raw.githubusercontent.com/catmaple/strm-manager/main/.env.example
cp .env.example .env
```

编辑 `.env`，替换所有 `CHANGE_ME` 示例值。管理员密码至少 12 位，Session Secret 至少 32 位。然后创建持久化目录并构建启动：

```sh
mkdir -p data/manager data/redis data/jellyfin/config data/jellyfin/cache \
  data/media/movies data/media/tv
sudo chown -R 999:999 data/redis
sudo chown -R 1000:1000 data/jellyfin/config data/jellyfin/cache
docker compose build --pull
docker compose up -d
docker compose ps
```

Docker 构建两个自定义镜像时会分别从 GitHub `main` 分支拉取同一仓库中的 `Dockerfile` 和 `jellyfin.Dockerfile` 所需文件。若 GitHub 无法访问或构建上下文下载失败，请先检查服务器到 GitHub 的网络连接。首次构建需要联网下载基础镜像及 Python 依赖。

上面是常见 Redis/Jellyfin 镜像的容器 UID；如果你使用的镜像版本采用不同 UID，请按该镜像要求调整宿主机目录所有者。STRM 管理容器以 root 身份运行，以便沿用现有媒体目录写入权限，同时已移除 Linux capabilities。

管理网页默认仅绑定在宿主机 `127.0.0.1:8080`，不会直接开放到局域网或公网。远程访问可通过 SSH 隧道：

```sh
ssh -L 8080:127.0.0.1:8080 user@服务器地址
```

然后在本机浏览器打开 <http://127.0.0.1:8080>。使用 `.env` 中的 `STRM_ADMIN_USER` 和 `STRM_ADMIN_PASSWORD` 登录。若要从可信局域网直接访问，可将 `compose.yaml` 中管理服务端口映射左侧的 `127.0.0.1` 改为服务器内网 IP；不要将管理端口直接暴露到公网。

DecoTV 和 Jellyfin 的端口分别是 3000、8096。请按现有方式完成 DecoTV 初始化、配置视频源、创建脚本专用用户，以及 Jellyfin 初始化和媒体库设置。

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

发布新的 GitHub 代码后，在服务器部署目录更新 Compose 和环境变量模板，并重新构建镜像：

```sh
curl -fsSLo compose.yaml https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose build --pull --no-cache
docker compose up -d
```

`--no-cache` 可避免复用之前构建的 GitHub 源码层。升级前备份 `data/` 和 `.env`。`.env` 包含登录凭据，不能公开分享。恢复时停止 Compose，将数据恢复到原目录后再启动。

Compose 默认跟随 GitHub `main` 分支。若要固定到一个稳定版本，可将 `compose.yaml` 中两个 `context` 的 `#main` 改为对应的 Git tag 或 commit。注意更新部署文件时也应下载同一个版本的 `compose.yaml`，确保部署配置与镜像源码一致。

## 已知范围

- 首版网页管理 `config.json`，没有提供网页手动启动任务；可使用上面的 `docker compose exec` 命令，或现有 Telegram 机器人。
- DecoTV 的 Redis 环境变量和登录 API 路径仍需按实际 DecoTV 镜像版本验证。当前配置默认采用手册中的 `REDIS_URL` 和 `/api/login`。
- 容器化脚本对 DecoTV 地址的必要适配仅使用 `decotv.url`，抓取、STRM 生成和线路选择逻辑沿用现有脚本。
- 在正式迁移前，先在测试环境验证 DecoTV 登录/代理、抓取、Jellyfin 扫描及 VidHub/Infuse/SenPlayer 播放；不要直接覆盖旧数据目录。
