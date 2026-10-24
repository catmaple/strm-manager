# DecoTV + STRM Manager

[简体中文](README.md) | English | [MIT License](LICENSE)

This project integrates DecoTV, Jellyfin, and the STRM management tools in a Docker Compose stack. It retrieves playback links through DecoTV and user-configured sources, generates STRM files for Jellyfin libraries, and enables browsing and playback through the Jellyfin web interface and compatible client apps.

The project stores only configuration and media metadata locally. Generated `.strm` files contain playback links from sources configured by the user and that the user is authorized to use. The project does not download or store video files; playback availability depends on those services and sources.

**This project does not provide, host, or distribute video content or video sources.** Users are responsible for configuring sources and ensuring they have lawful access to the content they use.

The existing STRM Python scripts, web configuration console, and scheduled jobs are packaged in the `strm-manager` container. Docker Compose starts four services:

| Service | Purpose | Address |
|---|---|---|
| DecoTV | Video-source browsing and request proxy | `http://SERVER_IP:3000` |
| Redis | DecoTV persistent storage | Compose network only |
| Jellyfin | Media library and playback server | `http://SERVER_IP:8096` |
| STRM Manager | Web console, scripts, and scheduler | `http://SERVER_IP:8080` by default |

Application configuration, Redis data, and Jellyfin data are stored under the deployment directory's `data/` folder. STRM files are stored separately on the host: `/media/movies` and `/media/shows`. Inside the containers, the scripts use `/media/movies` and `/media/tv`; Compose maps the host's `/media/shows` to `/media/tv`.

## Requirements

These instructions target Debian 12 or Ubuntu Linux. Allow at least 2 CPU cores and 4 GB RAM, plus sufficient storage for Jellyfin metadata and cache. Python, cron, and Redis do not need to be installed on the host.

Install Docker Engine and the Compose plugin:

```bash
apt update && apt upgrade -y
apt install -y curl ca-certificates openssl
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

docker version
docker compose version
```

## Install and configure

Create a deployment directory and download the Compose file and environment template:

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

Generate unique credentials and edit `.env`:

```bash
printf 'STRM_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 16)"
printf 'STRM_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)"
printf 'DECOTV_PASSWORD=%s\n' "$(openssl rand -hex 16)"
nano .env
```

Replace every `CHANGE_ME` value. Do not reuse the DecoTV password as the STRM Manager password.

| Variable | Purpose |
|---|---|
| `STRM_ADMIN_USER` | Web-console username |
| `STRM_ADMIN_PASSWORD` | Web-console password; at least 12 characters |
| `STRM_SESSION_SECRET` | Session signing secret; at least 32 characters |
| `STRM_PORT` | Host port for the console; default `8080` |
| `STRM_HOST_BIND` | Host interface for the console; default `0.0.0.0`, use `127.0.0.1` for local-only access |
| `TZ` | Container time zone; default `Asia/Shanghai` |
| `DECOTV_USERNAME` / `DECOTV_PASSWORD` | DecoTV administrator credentials |
| `STRM_MANAGER_IMAGE` / `JELLYFIN_IMAGE` | Image references; default to the published GHCR `latest` images |

Keep `.env` private and out of public repositories. Avoid unescaped `$` characters in values because Docker Compose performs variable interpolation.

Create persistent data and media directories:

```bash
mkdir -p \
  data/manager \
  data/redis \
  data/jellyfin/config \
  data/jellyfin/cache \
  /media/movies \
  /media/shows

chown -R 999:999 data/redis
chown -R 1000:1000 data/jellyfin/config data/jellyfin/cache
```

The UID/GID values above are common defaults; verify them for the image versions you deploy. STRM Manager must be able to write to `/media/movies` and `/media/shows`, and Jellyfin must be able to read them.

The deployment data layout is:

```text
data/
  manager/
    config.json       # Created from defaults at first startup
    crawl_state.json
    health.json
    logs/
    backups/
  redis/
  jellyfin/
    config/
    cache/

/media/
  movies/             # Movie STRM files
  shows/              # TV STRM files
```

## Start the stack

The published images are recommended; cloning the repository or building images is not needed for a normal deployment.

```bash
cd /opt/strm-manager
docker compose config -q
docker compose pull
docker compose up -d
docker compose ps
```

The stack uses `ghcr.io/decohererk/decotv:latest`, `redis:7-alpine`, `ghcr.io/catmaple/strm-manager-jellyfin:latest`, and `ghcr.io/catmaple/strm-manager:latest`. The Jellyfin image is based on the official Jellyfin image and installs the project's FFmpeg/FFprobe wrappers at Jellyfin's fixed executable paths. GitHub Actions publishes the project images when code is pushed to `main` or a `v*` tag is created. GHCR packages must be public for anonymous pulls.

## Verify services

Check container status and logs:

```bash
cd /opt/strm-manager
docker compose ps
docker compose logs --tail 50 decotv
docker compose logs --tail 50 decotv-redis
docker compose logs --tail 50 jellyfin
docker compose logs --tail 100 strm-manager
```

Check HTTP endpoints, Redis, and the Jellyfin FFmpeg wrappers:

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

Redis should return `PONG`; Jellyfin's health endpoint should return HTTP success (for example, `200 Healthy`). The wrapper executables and their original `.real` binaries should all exist. `ffmpeg -version` should exit successfully.

## First-time service setup

### DecoTV

Open `http://SERVER_IP:3000/login` and sign in with `DECOTV_USERNAME` and `DECOTV_PASSWORD` from `.env`.

1. Configure and verify the video sources in DecoTV.
2. Create a regular DecoTV account for the scripts (for example, `script`). Do not use the administrator account in the script configuration.
3. Verify source browsing, search, and playback.
4. If DecoTV is expected to use Redis, confirm its administration page does not report local-storage mode and inspect `data/redis/`.

If DecoTV logs Redis connection errors, verify the Redis environment-variable names supported by the deployed DecoTV version; these can change upstream.

### Jellyfin

Open `http://SERVER_IP:8096` and complete the initial setup.

1. Create a Jellyfin administrator.
2. Add a Movies library at `/media/movies`.
3. Add a Shows/TV library at `/media/tv` (mapped to the host's `/media/shows`).
4. Consider disabling chapter-image extraction and Trickplay/preview generation to reduce probing of remote STRM streams.
5. Create an API key under Dashboard → Advanced → API Keys and enter it in the STRM Manager configuration.
6. The FFmpeg path is fixed at `/usr/lib/jellyfin-ffmpeg/ffmpeg`. The custom image installs the wrapper there and keeps the original executable as `/usr/lib/jellyfin-ffmpeg/ffmpeg.real`; do not try to change the path to `/wrapper/ffmpeg` in Jellyfin.

To check a real HLS stream with the wrapper, use a valid playback URL:

```bash
docker exec jellyfin /usr/lib/jellyfin-ffmpeg/ffprobe -v error \
  -show_entries stream=codec_type,codec_name -of compact \
  -i "https://REPLACE_WITH_A_VALID_PLAYBACK_URL/index.m3u8"
```

## Configure STRM Manager

With the default bind address, open `http://SERVER_IP:8080` and sign in with `STRM_ADMIN_USER` and `STRM_ADMIN_PASSWORD`.

For local-only access, set this in `.env` and apply it:

```dotenv
STRM_HOST_BIND=127.0.0.1
```

```bash
docker compose up -d strm-manager
```

Then open <http://127.0.0.1:8080> on the server, or use an SSH tunnel from your workstation:

```bash
ssh -L 8080:127.0.0.1:8080 USER@SERVER_IP
```

The console and login page support Simplified Chinese and English. The language selector is available at the top of the page and its choice is stored in the current browser; changing the language does not alter configuration.

Do not expose the management console, DecoTV, or Jellyfin administration directly to the public internet. Use a VPN, SSH tunnel, or a properly secured HTTPS reverse proxy for remote access.

### Configuration and operations

The web form manages the existing `config.json` format. Use Docker service names inside the Compose network, not `127.0.0.1`. The initial configuration uses values such as:

```json
{
  "decotv": {
    "url": "http://decotv:3000"
  },
  "login": {
    "url": "http://decotv:3000/api/login",
    "username": "script",
    "password": "DECO TV SCRIPT ACCOUNT PASSWORD"
  },
  "jellyfin": {
    "url": "http://jellyfin:8096",
    "api_key": "JELLYFIN API KEY"
  },
  "crawl": {
    "via_decotv": true,
    "sources": []
  }
}
```

When the console opens, STRM Manager automatically syncs all enabled sources from DecoTV's admin configuration and previews their categories. The console does not provide controls to add or remove sources manually. If no sources are loaded, configure them in DecoTV first and make sure STRM Manager can reach the configured DecoTV URL. The **Validate and Save** button remains disabled until the source sync finishes. The console also supports scheduled jobs, connection tests, Dry Run, and manual DecoTV default-category scans, source-category scans, source rechecks, and statistics.

Only **Validate and Save** persists the submitted form. Test Connection, Dry Run, and manual job actions do not save pending form edits. Configuration saves keep up to 10 backups. Passwords, Telegram tokens, and Jellyfin API keys are masked; leaving a secret field blank preserves its current value. The full-config viewer also redacts secrets.

Some advanced fields, such as `list_urls`, are intentionally hidden from the form. Existing hidden fields are preserved when the form is saved. To edit hidden fields or remove a saved secret, back up and edit `data/manager/config.json` directly.

Validate the configuration and list configured source categories:

```bash
cd /opt/strm-manager
docker compose exec strm-manager python -m json.tool /data/config.json
docker compose exec strm-manager python /opt/m3u/crawl.py --types
```

`crawl.py --types` should list categories from configured sources. If `crawl.sources` is empty or a source is unreachable, fix the configuration before starting a full crawl.

## Test STRM generation and playback

Start with a small Dry Run:

```bash
docker compose exec strm-manager python /opt/m3u/crawl.py --dry --new 5 --pages 5
```

Review the preview and confirm movie/TV classification. Then run a small real crawl using a category ID returned by `--types`:

```bash
docker compose exec strm-manager python /opt/m3u/crawl.py --new 5 --pages 5 --type 6
```

Check generated files:

```bash
find /media/movies /media/shows -name '*.strm' -type f | head
```

Scan the libraries in Jellyfin and test playback in both the Jellyfin web player and the target client apps (VidHub, Infuse, or SenPlayer). Playback history and resume position are managed by Jellyfin and the client, not stored in STRM files.

Other useful commands:

```bash
docker compose exec strm-manager python /opt/m3u/add.py "TITLE"
docker compose exec strm-manager python /opt/m3u/recheck.py --dry --force
docker compose exec strm-manager python /opt/m3u/stats.py --no-send
```

## Scheduled jobs, manual runs, and logs

The default schedule uses the `TZ` time zone. The scheduled crawl uses `auto.py` by default; it can be switched to `crawl.py` in the console:

- Daily at 02:00: batch crawl
- Daily at 04:30: source recheck
- Sunday at 09:00: weekly statistics

The console lets you enable or disable the scheduler and individual jobs, change their run times, and choose `auto.py` (DecoTV default categories) or `crawl.py` (configured video sources) for the scheduled crawl. These are defaults and can be changed in the web form. The console also has buttons to start each task manually.

After a scheduled batch crawl (`auto.py` or `crawl.py`) completes successfully, movie reorganization runs automatically; there is no separate console button. It scans the TV library for single-season, single-file candidates and moves an item to Movies only when the configured CMS confirms it is a movie and its category, title/year, and STRM playback URL match unambiguously. Multi-episode directories, uncertain matches, and destination conflicts are left untouched and logged.

Movies can be misclassified when a CMS category is mapped as TV or multiple movie playback links are interpreted as episodes. Crawl classification now prefers the CMS item's category and explicit season information, falling back to the configured category mapping when metadata is unclear. Review `crawl.sources` mappings if misclassification continues.

Scheduled and manual jobs do not run concurrently.

Logs are persisted under `data/manager/logs/`:

```bash
docker compose logs -f strm-manager
tail -n 100 data/manager/logs/scheduler.log
tail -n 100 data/manager/logs/crawl.log
tail -n 100 data/manager/logs/recheck.log
```

To use the Telegram bot, configure `telegram.token` and at least one authorized ID in `telegram.allowed_chat_ids`. Enter multiple chat IDs as comma-separated numbers in the form. The bot only responds to authorized IDs and restarts automatically when its token or allowed-ID list changes.

### Telegram statistics and title search

1. Create a Telegram bot and copy its Bot Token into `telegram.token` in the web console.
2. Find your numeric Telegram Chat ID and enter it in `telegram.allowed_chat_ids`. Separate multiple IDs with commas. After saving, confirm the console reports that the Telegram bot is running.
3. Open a private chat with the bot and send `/start` or `/help` to see the available commands.

| Command or action | What it does |
|---|---|
| `/stats` or `/status` | Replies with movie/TV counts, crawl progress, source health, disk space, and DecoTV/Jellyfin status. It only reports the current state; it does not update the weekly statistics snapshot. |
| Type and send a title directly (no `/` command needed), for example `Example Movie` | Searches configured CMS APIs and returns buttons for available matches. Selecting one tests the routes, chooses a source, creates STRM files, and asks Jellyfin to scan the library. |
| `/add TITLE` | Searches for a title and adds it directly only when there is a unique match; otherwise, it presents choices. |
| `/scan` | Requests a Jellyfin library scan. |
| `/crawl` | Runs one source-category crawl, capped at 30 new items. |
| `/auto` | Runs one DecoTV chart/list crawl. |
| `/recheck` | Runs a source recheck. |

Search and add require enabled DecoTV sources that are reachable and return usable playback links; these sources are synced into STRM Manager's `cms_apis` configuration. If no Jellyfin API key is configured, wait for Jellyfin's scheduled scan or trigger a library scan manually. Only search for and add content you are authorized to access.

To obtain your Chat ID, send a message to the bot and read `message.chat.id` from the Telegram Bot API `getUpdates` response. Treat the Bot Token like a password. Never share API URLs containing the token, terminal output, or screenshots that expose it.

## Migrate an existing installation

Before migrating, stop the old cron/systemd jobs and bot to prevent old and new instances from writing to the same state files. Back up the full installation first.

| Existing data | New location |
|---|---|
| `/opt/m3u/config.json` | `data/manager/config.json` |
| `/opt/m3u/crawl_state.json`, `state.json`, `health.json`, `stats_snapshot.json` | `data/manager/` |
| DecoTV Redis data | `data/redis/` |
| Jellyfin `config/` | `data/jellyfin/config/` |
| Jellyfin `cache/` | `data/jellyfin/cache/` |
| Movie STRM files | `/media/movies/` |
| TV STRM files | `/media/shows/` |

If STRM files currently live under `/opt/strm-manager/data/media/`, stop crawling and copy them to the new media locations first. The legacy TV directory may be named `tv` or `shows`:

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

After copying, update Compose and recreate the containers that use the media mounts:

```bash
cd /opt/strm-manager
curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose config -q
docker compose up -d --force-recreate jellyfin strm-manager
```

Verify the STRM files, permissions, Jellyfin library scan, and client playback before deleting any old data or backups. In the migrated configuration, use `http://decotv:3000` and `http://jellyfin:8096` for service URLs.

## Update and build from source

To update a published deployment:

```bash
cd /opt/strm-manager
curl -fsSLo compose.yaml \
  https://raw.githubusercontent.com/catmaple/strm-manager/main/compose.yaml
docker compose pull
docker compose up -d
docker compose ps
```

Back up `data/` and `.env` before upgrades. To pin a release, set matching published image tags in `.env`:

```dotenv
STRM_MANAGER_IMAGE=ghcr.io/catmaple/strm-manager:v1.0.0
JELLYFIN_IMAGE=ghcr.io/catmaple/strm-manager-jellyfin:v1.0.0
```

Build locally only when modifying the source:

```bash
git clone https://github.com/catmaple/strm-manager.git
cd strm-manager
cp .env.example .env
# Edit .env and create persistent data directories.
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

## Troubleshooting

| Symptom | Checks |
|---|---|
| Console is unavailable | Run `docker compose ps` and `docker compose logs --tail 100 strm-manager`; verify the admin password is at least 12 characters and the session secret is at least 32 characters. |
| DecoTV reports local storage or Redis errors | Inspect `docker compose logs --tail 100 decotv decotv-redis`; verify the Redis environment variables supported by your DecoTV version. |
| Source categories cannot be discovered | Check CMS URLs, `crawl.sources`, `crawl.via_decotv`, and the DecoTV login endpoint/payload expected by the deployed version. |
| STRM files exist but Jellyfin shows no items | Check host paths `/media/movies` and `/media/shows`; in Jellyfin, library paths must be `/media/movies` and `/media/tv`. Then scan the library. |
| HLS playback fails | Confirm Jellyfin uses `/usr/lib/jellyfin-ffmpeg/ffmpeg`; test a real HLS URL with `/usr/lib/jellyfin-ffmpeg/ffprobe` and inspect the FFmpeg transcode log for the specific failure. |
| GHCR image pull fails | Confirm the image has been published and the GitHub Packages are public; authenticate with `docker login ghcr.io` if the packages are private. |

### Docker Hub IPv6 or registry connectivity errors

An error such as `dial tcp [IPv6 address]:443: connect: network is unreachable` occurs while connecting to Docker Hub; it is not a Redis or Compose configuration error. Check connectivity from the host:

```bash
# HTTP 401 is a normal anonymous response from the Docker registry.
curl -4 -I --connect-timeout 10 https://registry-1.docker.io/v2/

# This may fail if the host has no IPv6 route or egress.
curl -6 -I --connect-timeout 10 https://registry-1.docker.io/v2/
ip -6 route
```

If both IPv4 and IPv6 fail, configure Docker Engine to use a reachable proxy or a trusted registry mirror. If IPv4 works and IPv6 does not, repair IPv6 routing or configure the daemon to use the available network path. Shell proxy variables do not automatically apply to the Docker daemon. Avoid pinning a temporary CDN address in `/etc/hosts`.

Example systemd proxy configuration (replace the placeholders with your own proxy settings):

```bash
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://PROXY_HOST:PORT"
Environment="HTTPS_PROXY=http://PROXY_HOST:PORT"
Environment="NO_PROXY=localhost,127.0.0.1,.local"
EOF

systemctl daemon-reload
systemctl restart docker
docker info | grep -i proxy
```

Restarting Docker may interrupt running containers. Do this during a maintenance window. After fixing connectivity, retry the image pull:

```bash
cd /opt/strm-manager
docker compose pull
docker compose up -d
docker compose ps
```

## Important notes

- STRM files contain links; Jellyfin and the playback client manage watch history and resume progress.
- Verify DecoTV's Redis settings, login API path, and request fields against the deployed DecoTV version.
- Test source access, crawling, Jellyfin library scans, and playback in a non-production environment before migrating production data.
