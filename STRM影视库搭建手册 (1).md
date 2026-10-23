# 个人影视库搭建手册:DecoTV + Jellyfin + STRM 自动入库

> 目标:**不在本地保存任何视频**,把视频源(苹果 CMS 接口)里的播放地址生成 `.strm` 文件,交给 Jellyfin 入库,在 Apple TV 的 VidHub 上直接观看。
> 本手册用于以后**从零重建**。所有脚本均为最终整合版,按顺序复制粘贴即可。
> 整理日期:2026-10-19

---

## 0. 总览

```
视频源(苹果 CMS 接口)
        │
        ├── crawl.py   每晚批量抓取源的分类列表 ──┐
        ├── auto.py    抓取 DecoTV/豆瓣榜单(可选)  ├──► /media/movies/*.strm
        ├── add.py     命令行手动添加              │    /media/tv/*/Season NN/*.strm
        └── bot.py     Telegram 手机发片名添加 ────┘             │
                                                                 ▼
        recheck.py 定期复测,失效/变慢自动换线路            Jellyfin(含 ffmpeg 包装脚本)
                                                                 │
DecoTV + Redis(浏览、选源、登录、榜单接口)                      ▼
                                                         VidHub(Apple TV / iPhone)
```

| 组件 | 作用 | 端口 |
|---|---|---|
| DecoTV | 网页端浏览、源管理;脚本通过它转发源请求、读取榜单 | 3000 |
| Redis | DecoTV 的持久化存储(配置、用户、记录) | 内部 |
| Jellyfin | 媒体库,读取 STRM,给 VidHub 提供库 | 8096 |
| ffmpeg 包装脚本 | 让 Jellyfin 的 ffmpeg 接受"`.jpeg` 伪装切片"的 HLS | — |
| `/opt/m3u/*.py` | 生成 STRM、测速选线路、复测、Telegram 机器人 | — |

### 脚本职责一览

| 文件 | 职责 | 触发方式 |
|---|---|---|
| `probe.py` | 对 m3u8 线路测速(公共模块) | 被其他脚本调用 |
| `seasons.py` | 识别"第N季"、决定电影/剧集目录并写 STRM(公共模块) | 被其他脚本调用 |
| `crawl.py` | 按分类批量抓取源的列表并入库(主力) | cron 每晚 02:00 |
| `auto.py` | 按 DecoTV 榜单片名去源里搜索并入库(可选) | 手动 / Telegram |
| `add.py` | 命令行按片名添加(别名 `addvod`) | 手动 |
| `bot.py` | Telegram 机器人 | systemd 常驻 |
| `recheck.py` | 复测已入库线路,失效/变慢自动换线路 | cron 每晚 04:30 |
| `stats.py` | 每周统计(库存、本周新增、补库进度、线路健康、磁盘、服务状态)发到 Telegram | cron 每周日 09:00 / 机器人 `/stats` |
| `add_list.py` / `migrate_seasons.py` / `prune_old.py` | 辅助工具 | 手动 |

### 目录约定

```
/opt/decotv/docker-compose.yml          DecoTV + Redis
/opt/decotv/redis-data/                 Redis 数据
/opt/jellyfin/docker-compose.yml        Jellyfin
/opt/jellyfin/config|cache/             Jellyfin 数据、缓存
/opt/jellyfin/wrapper/ffmpeg|ffprobe    ffmpeg 包装脚本
/opt/m3u/                               全部脚本、config.json、状态文件
/media/movies/                          电影 STRM
/media/tv/                              剧集 STRM
```

---

## 1. 创建 PVE 容器

PVE 网页 → 创建 CT。**注意资源要给够**。

| 项 | 建议值 |
|---|---|
| 模板 | debian-12-standard or Ubuntu 22.04 standard|
| 特权 | **无特权容器** |
| 磁盘 | **20 GB 以上**(库越大元数据/海报越多) |
| CPU | **2 核**(Jellyfin 转码、探测会吃 CPU) |
| 内存 | **2 ~ 4 GB**,交换 1 GB |
| 网络 | vmbr0,**静态 IP**(本手册以 `192.168.1.110` 为例) |

创建后,**选项 → 功能**,勾选 **嵌套(nesting)** 和 **keyctl**,然后启动。**选项 → 开机自启动**设为"是"。

---

## 2. 系统基础软件

```bash
apt update && apt upgrade -y
apt install -y curl ca-certificates cron python3 util-linux
systemctl enable --now cron

# Docker
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
docker version
docker compose version
```

> 如果 apt 提示 locale 警告,可忽略;想消除:`apt install -y locales && dpkg-reconfigure locales`。
> 国内网络拉 Docker 镜像/ghcr.io 慢,需自行配置代理或镜像加速。

```bash
mkdir -p /opt/decotv /opt/jellyfin/wrapper /opt/m3u /media/movies /media/tv
```

---

## 3. 部署 DecoTV + Redis

```bash
cat > /opt/decotv/docker-compose.yml <<'EOF'
services:
  decotv:
    image: ghcr.io/decohererk/decotv:latest
    container_name: decotv
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - USERNAME=admin
      - PASSWORD=改成你自己的强密码
      - NEXT_PUBLIC_STORAGE_TYPE=redis
      - REDIS_URL=redis://decotv-redis:6379
    depends_on:
      - decotv-redis
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

  decotv-redis:
    image: redis:7-alpine
    container_name: decotv-redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - ./redis-data:/data
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
EOF

cd /opt/decotv && docker compose up -d
docker compose ps
docker compose logs --tail 30 decotv
```

**验证**

- 两个容器都是 `Up`;日志里没有 `ECONNREFUSED`、`Invalid NEXT_PUBLIC_STORAGE_TYPE`
- 浏览器打开 `http://192.168.1.110:3000/login`,用 `USERNAME`/`PASSWORD` 登录
- 进入 `/admin`,**"本地存储模式"的提示消失**,说明 Redis 生效。没消失就检查环境变量:
  `docker exec decotv env | grep -i -E "redis|storage"`
- 数据落盘:`ls /opt/decotv/redis-data`(能看到 `appendonlydir`)

> ⚠ `REDIS_URL` 这个变量名我没有从官方文档核实(官方示例用的是 Kvrocks 的 `KVROCKS_URL`)。如果提示仍是本地存储模式,请对照项目 README 的变量名。

### 3.1 配置视频源(自备)

`/admin` → 配置文件,按项目格式填写 `api_site`(苹果 CMS V10 接口)。保存后在"视频源"里确认每个源可用,首页搜索、播放测试一遍。

### 3.2 给脚本建专用账号

`/admin` → 用户管理 → 添加用户,例如 `script`,**普通用户**角色,单独密码。脚本用它登录取 Cookie,**不要在脚本里用 admin 账号**。

---

## 4. 部署 Jellyfin

### 4.1 ffmpeg 包装脚本(必需)

**为什么需要**:部分源的 HLS 播放列表把视频切片伪装成 `index0.jpeg` 这样的文件名,Jellyfin 自带 ffmpeg 默认只认 `.ts`/`.mp4` 等扩展名,日志会出现:

```
is not in allowed_segment_extensions
ffprobe failed - streams and format are both null
FFmpeg exited with code 183
```

表现为:该类剧集在 Jellyfin 网页端播放报 **500**,扫描时探测不到媒体信息。解决办法是在 ffmpeg 外面套一层脚本,遇到 `.m3u8` 输入时自动加上 `-allowed_segment_extensions ALL -extension_picky 0`。

```bash
cat > /opt/jellyfin/wrapper/ffmpeg <<'EOF'
#!/bin/bash
REAL=/usr/lib/jellyfin-ffmpeg/ffmpeg
args=()
inserted=0
argv=("$@")
for ((i=0; i<${#argv[@]}; i++)); do
  a="${argv[i]}"
  nxt="${argv[i+1]}"
  if [ "$a" = "-i" ] && [ $inserted -eq 0 ] && [[ "$nxt" == *.m3u8* ]]; then
    args+=(-allowed_segment_extensions ALL -extension_picky 0)
    inserted=1
  fi
  args+=("$a")
done
exec "$REAL" "${args[@]}"
EOF

cat > /opt/jellyfin/wrapper/ffprobe <<'EOF'
#!/bin/bash
REAL=/usr/lib/jellyfin-ffmpeg/ffprobe
args=()
inserted=0
argv=("$@")
for ((i=0; i<${#argv[@]}; i++)); do
  a="${argv[i]}"
  nxt="${argv[i+1]}"
  if [ "$a" = "-i" ] && [ $inserted -eq 0 ] && [[ "$nxt" == *.m3u8* ]]; then
    args+=(-allowed_segment_extensions ALL -extension_picky 0)
    inserted=1
  fi
  args+=("$a")
done
exec "$REAL" "${args[@]}"
EOF
chmod +x /opt/jellyfin/wrapper/ffmpeg /opt/jellyfin/wrapper/ffprobe
```

> 只在输入带 `.m3u8` 时才加选项:ffmpeg 对不认识的输入选项会直接报错退出,本地文件如果也被加了就播不了。

### 4.2 docker-compose

```bash
cat > /opt/jellyfin/docker-compose.yml <<'EOF'
services:
  jellyfin:
    image: jellyfin/jellyfin:latest
    container_name: jellyfin
    restart: unless-stopped
    ports:
      - "8096:8096"
    volumes:
      - ./config:/config
      - ./cache:/cache
      - /media:/media
      - ./wrapper:/wrapper:ro
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
EOF

cd /opt/jellyfin && docker compose up -d
sleep 20
docker ps --format '{{.Names}}  {{.Status}}'
docker exec jellyfin ls -l /wrapper
```

### 4.3 先单独验证包装脚本

需要有一条真实的 m3u8 地址(下面用一个 STRM 里的地址,没有 STRM 时换成你的源里任意一条 `.m3u8`):

```bash
U="https://你的源/xxx/index.m3u8"
docker exec jellyfin /wrapper/ffprobe -v error \
  -show_entries stream=codec_type,codec_name -of compact -i "$U"
```

**预期**:输出 `codec_name=h264`、`codec_name=aac` 之类。命令里没有手写那两个选项,是包装脚本自动加的。

### 4.4 Jellyfin 初始化

浏览器打开 `http://192.168.1.110:8096`,按向导走:

1. 语言选简体中文,创建管理员账号
2. **添加媒体库**(内容类型下拉框里没有 "TV",**"Shows" 就是电视剧**):

| 显示名 | 内容类型 | 文件夹 |
|---|---|---|
| 电影 | Movies | `/media/movies` |
| 节目 | Shows | `/media/tv` |

   不要选 "Mixed Movies and Shows",刮削容易出错。
3. 两个库的设置里:
   - 首选下载语言:Chinese;国家/地区:China
   - **关闭**章节图片提取(Enable chapter image extraction / Extract chapter images during library scan)和预览图生成(trickplay)。STRM 是远程流,这些功能要读整段视频,极慢且给源站增加压力(菜单名称随版本略有差异)
4. 右上角头像 → Settings → Display → Display language 可切换界面语言

### 4.5 创建 API 密钥(给脚本触发扫描)

Dashboard → Advanced → **API Keys** → 新建,起名 `strm-bot`,复制保存,稍后写入 `config.json` 的 `jellyfin.api_key`。

### 4.6 把 Jellyfin 切到包装脚本

Dashboard → Playback → **Transcoding** → **FFmpeg path** 填 `/wrapper/ffmpeg` → Save。

保存后确认没有反复重启:

```bash
sleep 20
docker ps --format '{{.Names}}  {{.Status}}'
docker logs jellyfin --tail 15 2>&1
```

**如果保存后 Jellyfin 反复重启,回滚:**

```bash
cd /opt/jellyfin && docker compose stop
cp /opt/jellyfin/config/config/encoding.xml /opt/jellyfin/config/config/encoding.xml.bak
sed -i 's|<EncoderAppPath>.*</EncoderAppPath>|<EncoderAppPath />|; s|<EncoderAppPathDisplay>.*</EncoderAppPathDisplay>|<EncoderAppPathDisplay />|' /opt/jellyfin/config/config/encoding.xml
docker compose start
```

### 4.7 清理转码缓存与日志(防止磁盘撑满)

```bash
cat > /etc/cron.d/clean-jellyfin <<'EOF'
0 5 * * * root find /opt/jellyfin/cache/transcodes -type f -mmin +120 -delete
30 5 * * 0 root find /opt/jellyfin/config/log -type f -mtime +7 -delete
EOF
chmod 644 /etc/cron.d/clean-jellyfin

cat > /etc/logrotate.d/strm <<'EOF'
/var/log/crawl-strm.log /var/log/recheck-strm.log /var/log/auto-strm.log /var/log/stats-strm.log {
    weekly
    rotate 4
    compress
    missingok
    notifempty
}
EOF
```

---

## 5. 脚本配置文件 `config.json`

所有脚本共用 `/opt/m3u/config.json`。**含密码和 Token,务必 `chmod 600`。**

```bash
cat > /opt/m3u/config.json <<'EOF'
{
  "list_urls": [],
  "cookie": "",
  "login": {
    "url": "http://127.0.0.1:3000/api/login",
    "username": "script",
    "password": "script用户的密码"
  },
  "cms_apis": [
    "https://hhzyapi.com/api.php/provide/vod"
  ],
  "max_new_per_run": 30,
  "max_pages": 20,
  "max_lookups_per_run": 300,
  "retry_miss_days": 3,
  "jellyfin": {
    "url": "http://127.0.0.1:8096",
    "api_key": "Jellyfin的API密钥"
  },
  "telegram": {
    "token": "",
    "allowed_chat_ids": []
  },
  "recheck": {
    "min_headroom": 1.5,
    "switch_gain": 1.3,
    "interval_days": 7,
    "max_per_run": 40
  },
  "crawl": {
    "via_decotv": true,
    "sources": [
      {
        "name": "百度资源",
        "api": "https://api.apibdzy.com/api.php/provide/vod/",
        "types": {
          "6": "movie", "7": "movie", "8": "movie", "9": "movie",
          "10": "movie", "11": "movie", "12": "movie",
          "13": "tv", "14": "tv", "15": "tv", "16": "tv", "17": "tv",
          "18": "tv", "19": "tv", "23": "tv",
          "25": "tv", "26": "tv", "27": "tv", "28": "tv"
        }
      }
    ],
    "max_age_years": 20,
    "min_year": 0,
    "skip_unknown_year": false,
    "min_score": 6,
    "score_field": "douban",
    "skip_remarks": ["TC", "TS", "CAM", "枪版", "抢先", "预告"],
    "max_new_per_run": 30,
    "max_pages_per_run": 150,
    "per_type_new": 15,
    "per_type_pages": 10,
    "backfill_max_pages": 25,
    "update_pages": 15,
    "max_eps": 60,
    "delay": 1.0
  }
}
EOF
chmod 600 /opt/m3u/config.json
python3 -m json.tool /opt/m3u/config.json > /dev/null && echo "格式正确"
```

> **"格式正确"没出现时不要继续**,先修 JSON。最常见的错误:多余的行、少逗号、从编辑器粘贴时带进了 `>` 之类的字符。
> 调整评分门槛示例:`c["crawl"]["min_score"] = 6.5`;关闭评分过滤:`c["crawl"]["min_score"] = 0`。
>
> 改单个字段不要用编辑器,用 Python 写入:
> ```bash
> python3 - <<'PY'
> import json
> p = "/opt/m3u/config.json"
> c = json.load(open(p, encoding="utf-8"))
> c["crawl"]["max_new_per_run"] = 50
> json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
> PY
> ```

### 配置项说明

| 字段 | 含义 |
|---|---|
| `login` | 脚本自动登录 DecoTV 的账号(用 3.2 建的 `script`)。如果 DecoTV 的转发/榜单接口不需要登录,可以整块删掉 |
| `cookie` | 手填 Cookie 的备用方式;`login` 失败时才会用 |
| `cms_apis` | 搜索用的视频源列表(`auto.py`、`add.py`、`bot.py`、`recheck.py` 使用),**多配几个源,选线路、换线路才有空间** |
| `jellyfin.api_key` | 为空则不会自动触发扫描,只能等 Jellyfin 自己定时扫描 |
| `telegram` | 第 8 节填写 |
| `crawl.sources[].types` | 分类 ID → `movie` / `tv`。ID 因源而异,用 `crawl.py --types` 查。**这个源的分类列表没有父类信息,所以必须手动指定电影/剧集** |
| `max_age_years` | 只收录近 N 年的作品,**动态计算**(2026 年 = 2006 年及以后) |
| `skip_unknown_year` | 源里没标年份的片:`false` 保留 / `true` 跳过 |
| `min_score` | 评分低于该值的不入库(默认 6);**没有评分的放行**;设为 0 关闭评分过滤 |
| `score_field` | 用哪个评分字段:`douban`(豆瓣分,默认,推荐)/ `vod`(源站自己的投票分)/ `any`(豆瓣优先,没有再用源站分) |
| `per_type_new` | 每个分类每晚最多新增 |
| `max_new_per_run` | 全部分类每晚最多新增(总上限,先到先停) |
| `backfill_max_pages` | 每个分类首次补库最多翻多少页(每页约 20 条),补完转入增量模式 |
| `max_eps` | 首次入库时集数超过该值的剧集跳过(综艺常有一百多集,拖慢扫描) |
| `recheck.min_headroom` | 速度余量低于该倍数视为太慢 |
| `recheck.switch_gain` | 新线路要比当前好这么多倍才切换,防抖动 |

---

## 6. 脚本代码

以下每个脚本都是**最终整合版**。依赖关系:`crawl.py` / `auto.py` / `add.py` / `bot.py` / `recheck.py` 都依赖 `seasons.py`;除 `crawl.py` 外都依赖 `probe.py`。

### 6.1 `probe.py` — 线路测速(公共模块)

对 m3u8 做真实下载测速:取首包耗时,多码率主列表选最高一档,下载**中间位置**两个切片(避开开头广告),算"实测下载速度 ÷ 播放所需码率"作为**余量**。余量超过 8 倍不再加分;单切片 12 秒下不完判为太慢。

```bash
cat > /opt/m3u/probe.py <<'EOF'
#!/usr/bin/env python3
import re, time, urllib.request, urllib.parse

UA = "Mozilla/5.0"
TIMEOUT = 8
SAMPLE = 2


def _open(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": UA}), timeout=TIMEOUT)


def _get_text(url):
    t0 = time.time()
    with _open(url) as r:
        ttfb = time.time() - t0
        data = r.read(2 * 1024 * 1024)
    return data.decode("utf-8", "ignore"), ttfb


def _download(url, limit=16 * 1024 * 1024, deadline=12.0):
    t0, n, complete = time.time(), 0, True
    with _open(url) as r:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            n += len(chunk)
            if n >= limit or time.time() - t0 > deadline:
                complete = False
                break
    return n, time.time() - t0, complete


def _parse_master(text, base):
    best, best_bw = None, -1
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", ln)
            bw = int(m.group(1)) if m else 0
            for nxt in lines[i + 1:]:
                if nxt.strip() and not nxt.startswith("#"):
                    if bw > best_bw:
                        best, best_bw = urllib.parse.urljoin(base, nxt.strip()), bw
                    break
    return best


def _parse_media(text, base):
    segs, dur = [], 0.0
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("#EXTINF:"):
            try:
                dur = float(ln[8:].split(",")[0])
            except ValueError:
                dur = 0.0
        elif ln and not ln.startswith("#"):
            segs.append((dur, urllib.parse.urljoin(base, ln)))
            dur = 0.0
    return segs


def score(url):
    """返回 {ttfb, mbps, need, headroom};失败返回 None。"""
    try:
        text, ttfb = _get_text(url)
        if "#EXT-X-STREAM-INF" in text:
            sub = _parse_master(text, url)
            if not sub:
                return None
            url = sub
            text, t2 = _get_text(url)
            ttfb += t2
        segs = _parse_media(text, url)
        if not segs:
            return None
        mid = len(segs) // 2
        sample = segs[mid:mid + SAMPLE] or segs[-SAMPLE:]
        tb, tt, td = 0, 0.0, 0.0
        for dur, su in sample:
            n, t, complete = _download(su)
            if not complete:
                return {"ttfb": ttfb, "mbps": n * 8 / max(t, 0.01) / 1e6,
                        "need": 1.0, "headroom": 0.1}
            tb, tt, td = tb + n, tt + t, td + dur
        td = td or 6.0 * len(sample)
        if tb == 0 or tt <= 0:
            return None
        mbps = tb * 8 / tt / 1e6
        need = tb * 8 / td / 1e6
        return {"ttfb": ttfb, "mbps": mbps, "need": need, "headroom": mbps / need}
    except Exception:
        return None


def pick(cands, log=print):
    """cands: [{'label':..., 'eps':[(名,url),...]}];返回最快的一个,并写入 _info。"""
    if not cands:
        return None
    nmax = max(len(c["eps"]) for c in cands)
    pool = [c for c in cands if len(c["eps"]) == nmax]
    if len(pool) == 1:
        pool[0]["_info"] = "仅一条线路,未测速"
        return pool[0]
    best, best_key = None, None
    for c in pool:
        s = score(c["eps"][0][1])
        name = c.get("label", "?")
        if s is None:
            c["_info"] = "测速失败"
            log(f"  线路 {name}: 不可用/超时")
            continue
        c["_info"] = f"{s['mbps']:.1f}Mbps/需{s['need']:.1f}, 首包{s['ttfb']:.2f}s"
        log(f"  线路 {name}: {c['_info']}")
        key = (min(s["headroom"], 8.0), -s["ttfb"])
        if best_key is None or key > best_key:
            best, best_key = c, key
    if best is None:
        pool[0]["_info"] = "全部测速失败,使用第一条"
        return pool[0]
    return best
EOF
python3 -m py_compile /opt/m3u/probe.py && echo "probe.py 语法检查通过"
```

**单独测一条线路:**

```bash
python3 - <<'PY'
import sys
sys.path.insert(0, "/opt/m3u")
import probe
print(probe.score("https://你的源/xxx/index.m3u8"))
PY
```

返回 `{'ttfb':…, 'mbps':…, 'need':…, 'headroom':…}`。`headroom` 参考:**< 1 必卡;1~1.5 勉强;≥ 2 流畅;≥ 8 非常充裕**。短时间内只下两个小切片,TCP 慢启动会使 `mbps` 偏低,适合比较线路相对快慢,不代表绝对带宽。

### 6.2 `seasons.py` — 多季识别与 STRM 写入(公共模块)

规则:

| 源里的名字 | 生成位置 |
|---|---|
| 某剧 / 某剧 第一季 | `/media/tv/某剧 (年份)/Season 01/某剧 S01E01.strm` |
| 某剧 第二季 / 第2季 / Season 2 / S2 | `/media/tv/某剧 (年份)/Season 02/…`(并入已有的同名剧集目录,第二季起年份可不同) |
| 只有 1 个 m3u8 且无"季"标记 | `/media/movies/片名 (年份)/片名 (年份).strm` |
| "第二部" | 只作为季号参考,**不单独**当剧集依据(避免电影续集被误判) |

```bash
cat > /opt/m3u/seasons.py <<'EOF'
#!/usr/bin/env python3
import os, re

TV_ROOT = "/media/tv"
MOVIE_ROOT = "/media/movies"

_CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
       "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})"
# (正则, 是否明确表示"季")。"第N部"只作季号参考,不单独当作剧集的证据
_PATS = [
    (re.compile(r"第\s*" + _NUM + r"\s*季"), True),
    (re.compile(r"第\s*" + _NUM + r"\s*部"), False),
    (re.compile(r"[Ss]eason\s*([0-9]{1,2})"), True),
    (re.compile(r"(?:^|\s)[Ss]([0-9]{1,2})$"), True),
]
_TAIL = r"[\s:：\-—–·、,，]+$"


def cn2int(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        tens = (_CN.get(a) if a else 1)
        ones = (_CN.get(b) if b else 0)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _CN.get(s)


def clean(s):
    s = re.sub(r"[\[\(（【].*?[\]\)）】]", "", s or "")
    s = re.sub(r'[\\/:*?"<>|]', " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_title(raw):
    """返回 (剧名, 季号, 是否明确标了季)。季号默认 1。"""
    s = raw or ""
    season, explicit = 1, False
    for pat, exp in _PATS:
        m = pat.search(s)
        if m:
            n = cn2int(m.group(1))
            if n and 0 < n < 100:
                season, explicit = n, exp
                s = s[:m.start()] + " " + s[m.end():]
                break
    base = re.sub(_TAIL, "", clean(s)).strip()
    return base, season, explicit


def split_folder(folder):
    m = re.match(r"^(.*?)\s*\((\d{4})\)$", folder)
    return (m.group(1), m.group(2)) if m else (folder, "")


def find_series_dir(base, year, season):
    """已有同名剧集目录就复用;后面的季可以和第一季年份不同。"""
    if os.path.isdir(TV_ROOT):
        for d in sorted(os.listdir(TV_ROOT)):
            b, y = split_folder(d)
            if b != base:
                continue
            if season > 1 or not year or not y or abs(int(y) - int(year)) <= 1:
                return d
    return f"{base} ({year})" if year else base


def is_tv(r):
    return len(r["eps"]) > 1 or bool(r.get("explicit"))


def write_strm(r):
    """r: name(剧名/片名), full(带季标记的完整名), season, explicit, year, eps"""
    eps, year = r["eps"], r.get("year", "")
    if not is_tv(r):
        name = r.get("full") or r["name"]
        folder = f"{name} ({year})" if year else name
        d = f"{MOVIE_ROOT}/{folder}"
        os.makedirs(d, exist_ok=True)
        open(f"{d}/{folder}.strm", "w", encoding="utf-8").write(eps[0][1] + "\n")
        return folder, len(eps)
    base, season = r["name"], r.get("season", 1)
    folder = find_series_dir(base, year, season)
    d = f"{TV_ROOT}/{folder}/Season {season:02d}"
    os.makedirs(d, exist_ok=True)
    for i, (_, u) in enumerate(eps, 1):
        open(f"{d}/{base} S{season:02d}E{i:02d}.strm", "w", encoding="utf-8").write(u + "\n")
    return f"{folder}/Season {season:02d}", len(eps)
EOF
python3 -m py_compile /opt/m3u/seasons.py && echo "seasons.py 语法检查通过"
```

**验证季识别:**

```bash
python3 - <<'PY'
import sys; sys.path.insert(0, "/opt/m3u")
import seasons
for t in ["某某剧 第二季", "某某剧第2季(国语)", "某某剧 Season 3", "某某剧 S2",
          "某某剧 第十二季", "某某剧 第一季", "哈利波特 第二部", "一个部门的诞生"]:
    print(f"{t!r:24} -> {seasons.parse_title(t)}")
PY
```

预期:前五条季号依次 2、2、3、2、12 且第三项为 `True`;"第一季"季号 1 且 `True`;"第二部"季号 2 但 `False`;最后一条季号 1、`False`。

### 6.3 `crawl.py` — 批量抓取(主力)

直接以视频源自己的分类列表为数据源(`ac=videolist` 每页 20 条,**自带 `vod_play_url`**),不需要逐部搜索。

特点:

- 每个分类**断点续做**,每晚只处理一批(`crawl_state.json` 记录进度)
- 每个分类有单晚上限,另有总上限;每天**轮换起始分类**,避免某个大分类占满额度
- 首次补库只翻到 `backfill_max_pages` 页(源默认按更新时间从新到旧),补完转入**增量模式**(每晚只看最新几页,连载剧集有新集会自动补上)
- 自动过滤:预告/花絮、枪版/抢先备注(`skip_remarks`)、超过 `max_age_years` 的老片、**评分低于 `min_score` 的片**、集数超过 `max_eps` 的首次入库
- 同名同年份已存在的**不覆盖**
- 批量入库**不测速**(几千部做不完),取集数最多的第一条 m3u8 线路,慢的/失效的交给 `recheck.py`
- 通过 DecoTV 的 `/api/proxy/cms` 转发请求(`via_decotv: true`)。如果容器能直连源站,可改成 `false`

```bash
cat > /opt/m3u/crawl.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, json, time, datetime, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import seasons

CONFIG = "/opt/m3u/config.json"
STATE = "/opt/m3u/crawl_state.json"
UA = "Mozilla/5.0"
DRY = "--dry" in sys.argv
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
cc = cfg.get("crawl", {})
state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
counters = {"added": 0, "pages": 0}


def arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        try:
            return int(sys.argv[i + 1])
        except Exception:
            pass
    return default


MAX_NEW = arg("--new", cc.get("max_new_per_run", 150))
MAX_PAGES = arg("--pages", cc.get("max_pages_per_run", 80))
TYPE_CAP = arg("--per-type", cc.get("per_type_new", 15))
TYPE_PAGES = cc.get("per_type_pages", 10)
MIN_YEAR = cc.get("min_year", 0)
if cc.get("max_age_years"):
    MIN_YEAR = datetime.date.today().year - int(cc["max_age_years"])
SKIP_REMARKS = [k.upper() for k in cc.get("skip_remarks", ["TC", "TS", "CAM", "枪版", "抢先", "预告"])]
MIN_SCORE = float(cc.get("min_score", 0))          # 0 = 不按评分过滤
SCORE_FIELDS = {"douban": ["vod_douban_score"], "vod": ["vod_score"],
                "any": ["vod_douban_score", "vod_score"]}.get(cc.get("score_field", "douban"), ["vod_douban_score"])


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


_COOKIE = None


def get_cookie():
    global _COOKIE
    if _COOKIE is not None:
        return _COOKIE
    lg = cfg.get("login")
    if not lg:
        _COOKIE = cfg.get("cookie", "")
        return _COOKIE
    body = json.dumps({"username": lg.get("username", ""), "password": lg["password"]}).encode()
    req = urllib.request.Request(lg["url"], data=body, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            _COOKIE = "; ".join(c.split(";", 1)[0].strip() for c in (r.headers.get_all("Set-Cookie") or []))
    except Exception as e:
        log("登录失败:", e)
        _COOKIE = cfg.get("cookie", "")
    return _COOKIE


def fetch(api, qs):
    target = api.rstrip("/") + "/?" + urllib.parse.urlencode(qs)
    headers = {"User-Agent": UA}
    if cc.get("via_decotv", False):
        url = "http://127.0.0.1:3000/api/proxy/cms?url=" + urllib.parse.quote(target, safe="")
        ck = get_cookie()
        if ck:
            headers["Cookie"] = ck
    else:
        url = target
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                return json.load(r)
        except Exception as e:
            log("  请求失败:", str(e)[:100], f"(第{i + 1}次)")
            time.sleep(2 * (i + 1))
    return None


def pick_eps(v):
    best = None
    for g in v.get("vod_play_url", "").split("$$$"):
        if ".m3u8" not in g:
            continue
        eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
        eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
        if eps and (best is None or len(eps) > len(best)):
            best = eps
    return best


def get_score(v):
    """返回评分;没有评分(空、0、无法解析)返回 0.0。"""
    for f in SCORE_FIELDS:
        try:
            x = float(str(v.get(f) or "0").strip() or 0)
        except ValueError:
            x = 0.0
        if x > 0:
            return x
    return 0.0


def skip_reason(v, raw):
    remarks = v.get("vod_remarks") or ""
    tags = raw + (v.get("type_name") or "") + remarks
    if any(k in tags for k in SKIP_WORDS):
        return "预告/花絮"
    if any(k in remarks.upper() for k in SKIP_REMARKS):
        return "低清/抢先"
    y = (v.get("vod_year") or "").strip()
    if MIN_YEAR and y.isdigit() and int(y) < MIN_YEAR:
        return "年份过早"
    if cc.get("skip_unknown_year") and not y.isdigit():
        return "无年份"
    if MIN_SCORE:
        sc = get_score(v)
        if 0 < sc < MIN_SCORE:      # 有评分且低于门槛才跳过;没有评分的放行
            return f"评分过低({sc})"
    return None


def exists_on_disk(r):
    if seasons.is_tv(r):
        d = seasons.find_series_dir(r["name"], r["year"], r["season"])
        return os.path.isdir(f"{seasons.TV_ROOT}/{d}/Season {r['season']:02d}")
    folder = f"{r['full']} ({r['year']})" if r["year"] else r["full"]
    return os.path.isdir(f"{seasons.MOVIE_ROOT}/{folder}")


def process_item(st, kind, v):
    """返回 (状态, 是否首次见到)。状态:added/updated/known/skipped"""
    vid = str(v.get("vod_id"))
    prev = st["seen"].get(vid)
    raw = v.get("vod_name", "")
    eps = pick_eps(v)
    n = len(eps) if eps else 0
    mx = cc.get("max_eps", 0)
    if mx and n > mx and not (prev and prev > 0):
        st["seen"][vid] = -1
        return "skipped", prev is None
    if prev is not None and prev > 0 and not (kind == "tv" and n > prev):
        return "known", False
    fresh = prev is None
    reason = skip_reason(v, raw)
    if reason or not eps:
        st["seen"][vid] = -1 if reason else 0
        return "skipped", fresh
    base, season, explicit = seasons.parse_title(raw)
    if not base:
        st["seen"][vid] = -1
        return "skipped", fresh
    year = (v.get("vod_year") or "").strip()
    year = year if year.isdigit() else ""
    r = {"name": base, "full": seasons.clean(raw), "season": season,
         "explicit": explicit or kind == "tv", "year": year, "eps": eps}
    key = f"{base}|{year}|{season}"
    owner = st["claimed"].get(key)
    if (owner and owner != vid) or (not owner and exists_on_disk(r)):
        st["seen"][vid] = -1  # 同名同年份已有,跳过,不覆盖
        return "skipped", fresh
    if DRY:
        log(f"  [dry] 将加入: {r['full']} ({year}) {'剧集' if seasons.is_tv(r) else '电影'} {n}个")
    else:
        folder, cnt = seasons.write_strm(r)
        log(f"  + {folder} ({cnt}个)")
    st["claimed"][key] = vid
    st["seen"][vid] = n
    return ("updated" if (prev or 0) > 0 else "added"), fresh


def save_state():
    if DRY:
        return
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def scan(src, tid, kind, st):
    update_mode = st["done"]
    page = 1 if update_mode else st["next"]
    walked = 0
    start_added = counters["added"]
    while True:
        if counters["added"] >= MAX_NEW or counters["pages"] >= MAX_PAGES:
            break
        if walked >= (cc.get("update_pages", 15) if update_mode else TYPE_PAGES):
            break
        data = fetch(src["api"], {"ac": "videolist", "pg": page, "t": tid})
        counters["pages"] += 1
        walked += 1
        if data is None:
            break
        items = data.get("list") or []
        if not items:
            if not update_mode:
                st["done"] = True
            break
        changed, partial = False, False
        for v in items:
            if counters["added"] >= MAX_NEW or counters["added"] - start_added >= TYPE_CAP:
                partial = True
                break
            status, fresh = process_item(st, kind, v)
            if status in ("added", "updated"):
                counters["added"] += 1
                changed = True
            elif fresh:
                changed = True
        if partial:
            if not update_mode:
                st["next"] = page      # 下次从同一页继续,已入库的会被跳过
            break
        if not update_mode:
            st["next"] = page + 1
            bmax = cc.get("backfill_max_pages", 0)
            if page >= int(data.get("pagecount") or 0) or (bmax and page >= bmax):
                st["done"] = True
                break
        elif not changed:
            break
        page += 1
        if counters["pages"] % 10 == 0:
            save_state()
        time.sleep(cc.get("delay", 1.0))


def list_types():
    for src in cc.get("sources", []):
        data = fetch(src["api"], {"ac": "list"})
        print(f"\n{src['name']}  {src['api']}")
        if not data:
            print("  获取分类失败")
            continue
        for c in data.get("class", []):
            d = fetch(src["api"], {"ac": "videolist", "pg": 1, "t": c["type_id"]})
            total = d.get("total") if d else "?"
            print(f"  t={c['type_id']:<4} {c['type_name']:<10} (父类 {c.get('type_pid')})  共 {total} 部")
            time.sleep(1)


def show_status():
    for k, st in state.items():
        n_ok = sum(1 for x in st["seen"].values() if x > 0)
        print(f"{k}: {'已补完' if st['done'] else '补库中,下次从第 %s 页' % st['next']},"
              f" 已记录 {len(st['seen'])} 条,已入库 {n_ok} 部")


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return
    req = urllib.request.Request(jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
                                 headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        log("已通知 Jellyfin 扫描")
    except Exception as e:
        log("通知 Jellyfin 失败:", e)


def notify(text):
    tgc = cfg.get("telegram")
    if not tgc or not tgc.get("token"):
        return
    for chat in tgc.get("allowed_chat_ids", []):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tgc['token']}/sendMessage",
                data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            log("Telegram 通知失败:", e)


def main():
    if "--types" in sys.argv:
        return list_types()
    if "--status" in sys.argv:
        return show_status()
    tasks = []
    for src in cc.get("sources", []):
        for tid, kind in src["types"].items():
            tasks.append((src, tid, kind, f"{src['name']}|{tid}"))
    if "--type" in sys.argv:
        _t = sys.argv[sys.argv.index("--type") + 1]
        tasks = [t for t in tasks if t[1] == _t]
    if not tasks:
        log("未配置 crawl.sources(或 --type 没有匹配的分类)")
        return
    off = int(time.time() // 86400) % len(tasks)       # 每天轮换起始分类
    tasks = tasks[off:] + tasks[:off]
    tasks.sort(key=lambda t: not state.get(t[3], {}).get("done", False))  # 已补完的先跑增量
    log(f"开始:只收录 {MIN_YEAR} 年及以后的作品,本次最多新增 {MAX_NEW} 部,最多翻 {MAX_PAGES} 页"
        + (" [dry]" if DRY else ""))
    for src, tid, kind, key in tasks:
        if counters["added"] >= MAX_NEW or counters["pages"] >= MAX_PAGES:
            break
        st = state.setdefault(key, {"next": 1, "done": False, "seen": {}, "claimed": {}})
        log(f"处理 {key} ({kind}):" + ("增量更新" if st["done"] else f"补库,从第 {st['next']} 页"))
        scan(src, tid, kind, st)
    save_state()
    log(f"完成:新增/更新 {counters['added']} 部,翻页 {counters['pages']} 次")
    if counters["added"] and not DRY:
        refresh_jellyfin()
        notify(f"批量抓取完成:新增/更新 {counters['added']} 部")


main()
EOF
chmod +x /opt/m3u/crawl.py
python3 -m py_compile /opt/m3u/crawl.py && echo "crawl.py 语法检查通过"
```

**用法**

```bash
python3 /opt/m3u/crawl.py --types                       # 列出源的所有分类 ID、名称、数量(要几十秒)
python3 /opt/m3u/crawl.py --dry --new 40 --pages 30     # 试运行,不写文件,不存进度
python3 /opt/m3u/crawl.py --new 20 --pages 20 --type 6  # 只跑某个分类(ID 6),真实写入
python3 /opt/m3u/crawl.py --status                      # 看各分类补库进度
```

参数:`--new N` 本次最多新增、`--pages N` 本次最多翻页、`--per-type N` 每分类上限、`--type ID` 只跑某分类、`--dry` 只预览。

**评分过滤的规则**

- 只有**有评分且低于 `min_score`** 才会被跳过;评分为 0、空、字段缺失的都放行(新片、冷门片、综艺大多没有评分)
- 默认只看**豆瓣评分**(`vod_douban_score`)。源站自己的 `vod_score` 是该站用户的投票分,很不可靠:同一部片的两个条目可能一个 5.0、一个 10.0,而豆瓣分都是 7.4
- 评分会随时间变化。被评分过滤跳过的片,只有在**增量模式**(每晚翻最新几页)再次出现时才会重新判断;补库阶段翻过去的页不会回头
- **只作用于 `crawl.py`**。手动添加(`addvod`、机器人)和 `auto.py` 不按评分过滤
- **不会清理已经入库的低分片**(STRM 里没有存评分)。想清理,见本节末尾的提示

> 因为这个源的分类列表没有父类信息,**分类的 `movie`/`tv` 要自己在 `config.json` 里指定**。分类名在 `--types` 的输出里看。名字像"动画电影"这类归在动漫下的,要手动设成 `movie`。

### 6.4 `auto.py` — 榜单驱动入库(可选)

按 DecoTV 分类页(豆瓣榜单)的片名,去 `cms_apis` 里搜索、**多线路测速**、选最快的写 STRM。优点是只入榜单上的热门片,缺点是依赖片名精确匹配,命中率有限。**主力用 `crawl.py`,这个脚本可不启用。**

使用前需要在 `config.json` 的 `list_urls` 里填榜单接口地址。获取方法:浏览器登录 DecoTV,F12 → 网络 → Fetch/XHR,点一个分类标签,复制 `douban/categories` 请求的地址,用 6.8 的 `add_list.py` 写入(它会自动规范成本机地址、`start` 归零、去重)。

```bash
cat > /opt/m3u/auto.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, json, time, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
STATE = "/opt/m3u/state.json"
DRY = "--dry" in sys.argv
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def http_json(url, cookie=""):
    h = {"User-Agent": UA}
    if cookie:
        h["Cookie"] = cookie
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25) as r:
        return json.load(r)


def find_items(o):
    if isinstance(o, list) and o and isinstance(o[0], dict) and any(k in o[0] for k in ("title", "name")):
        return o
    if isinstance(o, dict):
        for v in o.values():
            r = find_items(v)
            if r:
                return r
    return []


_COOKIE = None


def get_cookie():
    """每次运行登录一次拿新 Cookie;没配置 login 则使用 config 里手填的 cookie。"""
    global _COOKIE
    if _COOKIE is not None:
        return _COOKIE
    lg = cfg.get("login")
    if not lg:
        _COOKIE = cfg.get("cookie", "")
        return _COOKIE
    body = json.dumps({"username": lg.get("username", ""), "password": lg["password"]}).encode()
    req = urllib.request.Request(
        lg["url"], data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            parts = [c.split(";", 1)[0].strip() for c in (r.headers.get_all("Set-Cookie") or [])]
            _COOKIE = "; ".join(parts)
    except Exception as e:
        log("登录失败:", e)
        _COOKIE = cfg.get("cookie", "")
    log("登录成功,已获取 Cookie" if _COOKIE else "警告:未获得 Cookie")
    return _COOKIE


def with_start(url, start):
    p = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    found = False
    out = []
    for k, v in q:
        if k == "start":
            out.append((k, str(start)))
            found = True
        else:
            out.append((k, v))
    if not found:
        out.append(("start", str(start)))
    return urllib.parse.urlunparse(p._replace(query=urllib.parse.urlencode(out)))


def get_titles():
    seen, out = set(), []
    max_pages = cfg.get("max_pages", 20)
    for u in cfg["list_urls"]:
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query))
        try:
            limit = int(qs.get("limit", 25))
        except ValueError:
            limit = 25
        total, pages = 0, 0
        for page in range(max_pages):
            pu = with_start(u, page * limit)
            try:
                data = http_json(pu, get_cookie())
            except Exception as e:
                log("取榜单失败:", pu, e)
                break
            items = find_items(data)
            if not items:
                break
            pages += 1
            total += len(items)
            for it in items:
                t = seasons.clean(it.get("title") or it.get("name") or "")
                m = re.search(r"(19|20)\d{2}", str(it.get("year") or ""))
                y = m.group(0) if m else ""
                if t and (t, y) not in seen:
                    seen.add((t, y))
                    out.append((t, y))
            time.sleep(0.5)
            if len(items) < limit:
                break
        log(f"榜单 {pages} 页 / {total} 条:", u[:110])
    return out


def search(api, title, year):
    base, season, _ = seasons.parse_title(title)
    full = seasons.clean(title)
    url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(base)}"
    try:
        data = http_json(url)
    except Exception as e:
        log("  搜索失败:", api, e)
        return []
    host = urllib.parse.urlparse(api).netloc
    out = []
    for v in data.get("list", []):
        raw = v.get("vod_name", "")
        tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
        if any(k in tags for k in SKIP_WORDS):
            continue
        b, sn, explicit = seasons.parse_title(raw)
        if not ((b == base and sn == season) or seasons.clean(raw) == full):
            continue
        vy = (v.get("vod_year") or "").strip()
        if year and vy.isdigit() and abs(int(vy) - int(year)) > 1:
            continue
        groups = v.get("vod_play_url", "").split("$$$")
        froms = v.get("vod_play_from", "").split("$$$")
        for i, g in enumerate(groups):
            if ".m3u8" not in g:
                continue
            eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
            eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
            if eps:
                ln = froms[i] if i < len(froms) else f"线路{i}"
                out.append({"name": b, "full": seasons.clean(raw), "season": sn,
                            "explicit": explicit, "year": vy if vy.isdigit() else year,
                            "eps": eps, "label": f"{host}/{ln}"})
    return out


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        log("已通知 Jellyfin 扫描")
    except Exception as e:
        log("通知 Jellyfin 失败:", e)


def main():
    titles = get_titles()
    log(f"共发现 {len(titles)} 部不重复的片")
    if "--titles" in sys.argv:
        return
    if not titles:
        log("没有取到任何片名,请检查 list_urls 和登录/cookie 配置")
        return
    added, now, lookups = 0, time.time(), 0
    retry = cfg.get("retry_miss_days", 3) * 86400
    for title, year in titles:
        if added >= cfg.get("max_new_per_run", 30):
            log("已达到本次新增上限")
            break
        key = f"{title}|{year}"
        st = state.get(key)
        if st and (st["s"] == "ok" or now - st["t"] < retry):
            continue
        if lookups >= cfg.get("max_lookups_per_run", 300):
            log("已达到本次查找上限,剩余的留到下次")
            break
        lookups += 1
        if lookups % 20 == 0 and not DRY:
            json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        log("查找:", title, year)
        cands = []
        for api in cfg["cms_apis"]:
            cands += search(api, title, year)
            time.sleep(1)
        hit = probe.pick(cands, log) if cands else None
        if hit:
            log("  选用线路:", hit.get("label"), "|", hit.get("_info"))
        if not hit:
            log("  未找到")
            state[key] = {"s": "miss", "t": now}
            continue
        if DRY:
            log(f"  [dry] 将生成: {hit['full']} ({hit['year']}) {len(hit['eps'])} 个")
            continue
        folder, n = seasons.write_strm(hit)
        log(f"  已生成: {folder} ({n} 个)")
        state[key] = {"s": "ok", "t": now}
        added += 1
    if not DRY:
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
        if added:
            refresh_jellyfin()
    log(f"完成,本次新增 {added} 部")


main()
EOF
chmod +x /opt/m3u/auto.py
python3 -m py_compile /opt/m3u/auto.py && echo "auto.py 语法检查通过"
```

**用法**

```bash
python3 /opt/m3u/auto.py --titles    # 只统计榜单里有多少部不重复的片
python3 /opt/m3u/auto.py --dry       # 试运行(会搜索、会测速,不写文件)
python3 /opt/m3u/auto.py             # 正式运行
```

### 6.5 `add.py` — 命令行手动添加(`addvod`)

按片名搜索所有 `cms_apis`,**对同一部片的所有线路测速**,选最快的写 STRM。名字带"第N季"会自动归到对应季。**不应用年份过滤**(你主动点名要的片不拦)。

```bash
cat > /opt/m3u/add.py <<'EOF'
#!/usr/bin/env python3
import sys, json, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import seasons, probe

cfg = json.load(open("/opt/m3u/config.json", encoding="utf-8"))
if len(sys.argv) < 2:
    print('用法: add.py "片名 [第N季]"')
    sys.exit(1)

title = sys.argv[1]
tb, ts, _ = seasons.parse_title(title)
full = seasons.clean(title)
groups = {}
for api in cfg["cms_apis"]:
    url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(tb)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = json.load(urllib.request.urlopen(req, timeout=20))
    except Exception as e:
        print("搜索失败:", api, e)
        continue
    host = urllib.parse.urlparse(api).netloc
    for v in data.get("list", []):
        raw = v.get("vod_name", "")
        tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
        if any(k in tags for k in ("预告", "花絮", "特辑")):
            continue
        b, sn, ex = seasons.parse_title(raw)
        if not ((b == tb and sn == ts) or seasons.clean(raw) == full):
            continue
        vy = (v.get("vod_year") or "").strip()
        vy = vy if vy.isdigit() else ""
        plays = v.get("vod_play_url", "").split("$$$")
        froms = v.get("vod_play_from", "").split("$$$")
        for i, g in enumerate(plays):
            if ".m3u8" not in g:
                continue
            eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
            eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
            if not eps:
                continue
            c = groups.setdefault((b, sn, vy), {
                "name": b, "full": seasons.clean(raw), "season": sn,
                "explicit": ex, "year": vy, "lines": []})
            ln = froms[i] if i < len(froms) else f"线路{i}"
            c["lines"].append({"label": f"{host}/{ln}", "eps": eps})

if not groups:
    print("没找到可用的 m3u8 资源")
    sys.exit(1)
for c in groups.values():
    best = probe.pick(c["lines"])
    c["eps"] = best["eps"]
    folder, n = seasons.write_strm(c)
    print(f"已生成: {folder} ({n} 个) 线路 {best['label']}")
EOF
chmod +x /opt/m3u/add.py
python3 -m py_compile /opt/m3u/add.py && echo "add.py 语法检查通过"

# 命令别名:addvod "片名"
sed -i '/^addvod()/d' ~/.bashrc
echo 'addvod() { python3 /opt/m3u/add.py "$1"; }' >> ~/.bashrc
source ~/.bashrc
```

用法:`addvod "某某剧 第二季"`。不带季号时只加第一季。

### 6.6 `bot.py` — Telegram 机器人

| 你发送 | 机器人做什么 |
|---|---|
| `星际穿越`(直接发片名) | 搜索,候选(片名、年份、电影/第N季·集数、语言)做成按钮,点哪个加哪个 |
| `/add 片名` | 只有一个完全同名结果时直接加,否则给按钮 |
| `/crawl` | 立即运行一次批量抓取(`crawl.py --new 30`) |
| `/auto` | 运行 `auto.py`(榜单驱动) |
| `/recheck` | 运行一次线路复测 |
| `/stats` 或 `/status` | 立即查看统计(不会影响每周周报里的"本周新增") |
| `/scan` | 通知 Jellyfin 扫描 |

长轮询拉消息,**不需要对外开放端口**。**只有 `allowed_chat_ids` 里的用户**能操作。点候选后会对该片所有线路测速,再写 STRM。

```bash
cat > /opt/m3u/bot.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, json, time, subprocess, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

cfg = json.load(open(CONFIG, encoding="utf-8"))
TG = cfg["telegram"]
ALLOWED = {int(x) for x in TG.get("allowed_chat_ids", [])}
API = f"https://api.telegram.org/bot{TG['token']}/"
cache = {}  # chat_id -> 最近一次搜索的候选列表


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def tg(method, **params):
    req = urllib.request.Request(
        API + method, data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=65) as r:
        return json.load(r)


def say(chat, text, buttons=None):
    p = {"chat_id": chat, "text": text[:4000]}
    if buttons:
        p["reply_markup"] = {"inline_keyboard": buttons}
    try:
        tg("sendMessage", **p)
    except Exception as e:
        log("发送失败:", e)


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def candidates(title):
    tbase, tseason, _ = seasons.parse_title(title)
    out = {}
    for api in cfg["cms_apis"]:
        url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(tbase)}"
        try:
            data = http_json(url)
        except Exception as e:
            log("搜索失败:", api, e)
            continue
        host = urllib.parse.urlparse(api).netloc
        for v in data.get("list", []):
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            base, sn, explicit = seasons.parse_title(raw)
            if not base:
                continue
            vy = (v.get("vod_year") or "").strip()
            vy = vy if vy.isdigit() else ""
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            for i, g in enumerate(groups):
                if ".m3u8" not in g:
                    continue
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
                if not eps:
                    continue
                c = out.setdefault((base, sn, vy), {
                    "name": base, "full": seasons.clean(raw), "season": sn,
                    "explicit": explicit, "year": vy,
                    "lang": v.get("vod_lang") or "", "lines": []})
                ln = froms[i] if i < len(froms) else f"线路{i}"
                c["lines"].append({"label": f"{host}/{ln}", "eps": eps})
    res = list(out.values())
    for c in res:
        c["eps"] = max(c["lines"], key=lambda l: len(l["eps"]))["eps"]  # 仅用于显示
    res.sort(key=lambda c: not (c["name"] == tbase and c["season"] == tseason))
    return res[:8]


def label(c):
    tv = seasons.is_tv({"eps": c["eps"], "explicit": c["explicit"]})
    kind = f"第{c['season']}季 · {len(c['eps'])}集" if tv else "电影"
    nm = c["name"] if tv else c["full"]
    year = f" ({c['year']})" if c["year"] else ""
    lang = f" · {c['lang']}" if c["lang"] else ""
    return f"{nm}{year} · {kind}{lang}"


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return False
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception as e:
        log("通知 Jellyfin 失败:", e)
        return False


def do_add(chat, c):
    say(chat, "正在测速,选择最快的线路……")
    best = probe.pick(c["lines"], log)
    folder, _ = seasons.write_strm({
        "name": c["name"], "full": c["full"], "season": c["season"],
        "explicit": c["explicit"], "year": c["year"], "eps": best["eps"]})
    ok = refresh_jellyfin()
    tail = "已通知 Jellyfin 扫描,稍等一两分钟在 VidHub 里刷新。" if ok \
        else "未配置 Jellyfin api_key,请等它定时扫描或手动扫描。"
    say(chat, f"✅ 已加入:{label(c)}\n线路:{best.get('label')}({best.get('_info', '')})\n目录:{folder}\n{tail}")


def search_and_reply(chat, title, direct=False):
    say(chat, f"正在搜索:{title}")
    cands = candidates(title)
    if not cands:
        say(chat, "没找到可用的 m3u8 资源,换个关键词或换个源试试。")
        return
    tb, ts, _ = seasons.parse_title(title)
    exact = [c for c in cands if c["name"] == tb and c["season"] == ts]
    if direct and len(exact) == 1:
        do_add(chat, exact[0])
        return
    cache[chat] = cands
    buttons = [[{"text": label(c)[:60], "callback_data": f"add:{i}"}]
               for i, c in enumerate(cands)]
    say(chat, "请选择要加入的版本:", buttons)


def run_script(chat, script, extra, title):
    say(chat, f"开始运行{title},可能需要几分钟……")
    try:
        r = subprocess.run([sys.executable, script] + extra,
                           capture_output=True, text=True, timeout=3600)
        lines = (r.stdout + r.stderr).strip().splitlines()
        say(chat, "完成:\n" + "\n".join(lines[-12:]))
    except Exception as e:
        say(chat, f"运行失败:{e}")


def handle(u):
    if "callback_query" in u:
        cq = u["callback_query"]
        chat = cq["message"]["chat"]["id"]
        if chat not in ALLOWED:
            return
        try:
            tg("answerCallbackQuery", callback_query_id=cq["id"])
        except Exception:
            pass
        data = cq.get("data", "")
        if data.startswith("add:"):
            cands = cache.get(chat) or []
            i = int(data[4:])
            if i < len(cands):
                do_add(chat, cands[i])
            else:
                say(chat, "列表已过期,请重新发送片名搜索。")
        return

    m = u.get("message")
    if not m:
        return
    chat = m["chat"]["id"]
    if chat not in ALLOWED:
        log("忽略未授权用户:", chat)
        return
    text = (m.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        say(chat, "发送片名:搜索并选择\n/add 片名:同名唯一时直接加入\n"
                  "/crawl:批量抓取一次\n/auto:榜单抓取\n/recheck:复测线路\n/stats 或 /status:查看统计\n/scan:通知 Jellyfin 扫描")
    elif text.startswith("/add"):
        title = text[4:].strip()
        if title:
            search_and_reply(chat, title, direct=True)
        else:
            say(chat, "用法:/add 片名")
    elif text == "/crawl":
        run_script(chat, "/opt/m3u/crawl.py", ["--new", "30"], "批量抓取")
    elif text == "/auto":
        run_script(chat, "/opt/m3u/auto.py", [], "榜单抓取")
    elif text == "/recheck":
        run_script(chat, "/opt/m3u/recheck.py", [], "线路复测")
    elif text in ("/stats", "/status"):
        try:
            r = subprocess.run([sys.executable, "/opt/m3u/stats.py", "--no-send"],
                               capture_output=True, text=True, timeout=120)
            say(chat, (r.stdout or r.stderr).strip() or "无输出")
        except Exception as e:
            say(chat, f"统计失败:{e}")
    elif text == "/scan":
        say(chat, "已通知 Jellyfin 扫描" if refresh_jellyfin() else "通知失败或未配置 api_key")
    elif text.startswith("/"):
        say(chat, "未知命令,发送 /help 查看用法。")
    else:
        search_and_reply(chat, text)


def main():
    log("机器人启动,授权用户:", sorted(ALLOWED))
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        try:
            res = tg("getUpdates", **params)
        except Exception as e:
            log("getUpdates 失败:", e)
            time.sleep(5)
            continue
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            try:
                handle(u)
            except Exception as e:
                log("处理出错:", e)


main()
EOF
chmod +x /opt/m3u/bot.py
python3 -m py_compile /opt/m3u/bot.py && echo "bot.py 语法检查通过"
```

> 机器人是串行处理消息的:`/crawl`、`/recheck` 运行期间(可能几分钟)不会响应其他消息。这几个命令和 cron 任务**没有互斥**(cron 用 `flock`,机器人没有),避免在凌晨 2~5 点手动触发。

### 6.7 `recheck.py` — 定期复测线路

每次运行:

1. 扫描 `/media/movies` 和 `/media/tv` 里的 STRM(**剧集按每一季单独处理**),测当前线路(第 1 集)
2. **健康的**(余量 ≥ `min_headroom`)直接跳过,不发搜索请求
3. 失效或太慢的:去所有 `cms_apis` 重新搜这部片,逐个候选测速
4. 找到明显更好的(好 `switch_gain` 倍以上)就**覆盖 STRM**;剧集只考虑集数**不少于**现有集数的线路,不会丢集
5. 健康的片 `interval_days` 天后再查,失效/太慢的 1 天后再查;每次最多查 `max_per_run` 部,最久没查的优先
6. 有改动时通知 Jellyfin 扫描并给 Telegram 发汇总

```bash
cat > /opt/m3u/recheck.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, json, time, urllib.request, urllib.parse
sys.path.insert(0, "/opt/m3u")
import probe
import seasons

CONFIG = "/opt/m3u/config.json"
HEALTH = "/opt/m3u/health.json"
UA = "Mozilla/5.0"
SKIP_WORDS = ("预告", "花絮", "特辑")

DRY = "--dry" in sys.argv
OPT = "--optimize" in sys.argv
FORCE = "--force" in sys.argv
ONLY = None
if "--only" in sys.argv:
    i = sys.argv.index("--only")
    ONLY = sys.argv[i + 1] if i + 1 < len(sys.argv) else None

cfg = json.load(open(CONFIG, encoding="utf-8"))
rc = cfg.get("recheck", {})
MIN_HR = rc.get("min_headroom", 1.5)      # 低于此余量视为"太慢"
GAIN = rc.get("switch_gain", 1.3)         # 新线路至少好这么多倍才切换
OK_DAYS = rc.get("interval_days", 7)      # 健康的片多久复查一次
MAX_RUN = rc.get("max_per_run", 40)       # 每次最多检查几部
health = json.load(open(HEALTH, encoding="utf-8")) if os.path.exists(HEALTH) else {}


def log(*a):
    print(time.strftime("%F %T"), *a, flush=True)


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def fmt(s):
    if not s:
        return "失败/不可用"
    return f"{s['mbps']:.1f}Mbps/需{s['need']:.1f} (余量{s['headroom']:.1f}x, 首包{s['ttfb']:.2f}s)"


def split_name(folder):
    m = re.match(r'^(.*?)\s*\((\d{4})\)$', folder)
    return (m.group(1), m.group(2)) if m else (folder, "")


def read_url(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return ""


def scan_library():
    items = []
    base = "/media/movies"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            p = f"{base}/{d}"
            if not os.path.isdir(p):
                continue
            files = sorted(f for f in os.listdir(p) if f.endswith(".strm"))
            if files:
                items.append({"kind": "movie", "key": "movies/" + d, "folder": d,
                              "label": d, "season": 1, "dir": p, "files": files})
    base = "/media/tv"
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            sd = f"{base}/{d}"
            if not os.path.isdir(sd):
                continue
            for s in sorted(os.listdir(sd)):
                m = re.match(r"^Season (\d+)$", s)
                p = f"{sd}/{s}"
                if not m or not os.path.isdir(p):
                    continue
                files = sorted(f for f in os.listdir(p) if f.endswith(".strm"))
                if files:
                    n = int(m.group(1))
                    items.append({"kind": "tv", "key": f"tv/{d}/{s}", "folder": d,
                                  "label": f"{d} 第{n}季", "season": n,
                                  "dir": p, "files": files})
    return items


def candidates(name, year, season=1):
    out = []
    for api in cfg["cms_apis"]:
        url = f"{api}{'&' if '?' in api else '?'}ac=detail&wd={urllib.parse.quote(name)}"
        try:
            data = http_json(url)
        except Exception as e:
            log("  搜索失败:", api, e)
            continue
        host = urllib.parse.urlparse(api).netloc
        for v in data.get("list", []):
            raw = v.get("vod_name", "")
            tags = raw + (v.get("type_name") or "") + (v.get("vod_remarks") or "")
            if any(k in tags for k in SKIP_WORDS):
                continue
            b, sn, _ = seasons.parse_title(raw)
            if not ((b == name and sn == season) or (season == 1 and seasons.clean(raw) == name)):
                continue
            vy = (v.get("vod_year") or "").strip()
            if season == 1 and year and vy.isdigit() and abs(int(vy) - int(year)) > 1:
                continue
            groups = v.get("vod_play_url", "").split("$$$")
            froms = v.get("vod_play_from", "").split("$$$")
            for i, g in enumerate(groups):
                if ".m3u8" not in g:
                    continue
                eps = [e.split("$", 1) for e in g.split("#") if "$" in e]
                eps = [(n, u.strip()) for n, u in eps if ".m3u8" in u]
                if eps:
                    ln = froms[i] if i < len(froms) else f"线路{i}"
                    out.append({"label": f"{host}/{ln}", "eps": eps})
        time.sleep(1)
    return out


def write_back(it, name, eps):
    if it["kind"] == "movie":
        open(f"{it['dir']}/{it['files'][0]}", "w", encoding="utf-8").write(eps[0][1] + "\n")
    else:
        sn = it.get("season", 1)
        for i, (_, u) in enumerate(eps, 1):
            open(f"{it['dir']}/{name} S{sn:02d}E{i:02d}.strm", "w", encoding="utf-8").write(u + "\n")


def due(it, now):
    h = health.get(it["key"])
    if FORCE or not h:
        return True
    days = 1 if h["s"] in ("dead", "slow") else OK_DAYS
    return now - h["t"] >= days * 86400


def refresh_jellyfin():
    jf = cfg.get("jellyfin", {})
    if not jf.get("api_key"):
        return
    req = urllib.request.Request(
        jf["url"].rstrip("/") + "/Library/Refresh", method="POST",
        headers={"Authorization": f'MediaBrowser Token="{jf["api_key"]}"'})
    try:
        urllib.request.urlopen(req, timeout=15)
        log("已通知 Jellyfin 扫描")
    except Exception as e:
        log("通知 Jellyfin 失败:", e)


def notify(text):
    tgc = cfg.get("telegram")
    if not tgc or not tgc.get("token"):
        return
    for chat in tgc.get("allowed_chat_ids", []):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tgc['token']}/sendMessage",
                data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            log("Telegram 通知失败:", e)


def main():
    items = scan_library()
    if ONLY:
        items = [i for i in items if ONLY in i["folder"]]
    now = time.time()
    todo = [i for i in items if due(i, now)]
    todo.sort(key=lambda i: health.get(i["key"], {}).get("t", 0))
    todo = todo[:MAX_RUN]
    log(f"库中共 {len(items)} 部/季,本次检查 {len(todo)} 个"
        + (" [dry]" if DRY else "") + (" [optimize]" if OPT else ""))

    fixed, unfixed, ok_n = [], [], 0
    for it in todo:
        name, year = split_name(it["folder"])
        cur_url = read_url(f"{it['dir']}/{it['files'][0]}")
        cur = probe.score(cur_url) if cur_url else None
        hr = cur["headroom"] if cur else 0.0
        healthy = hr >= MIN_HR
        log(f"检查: {it['label']} | 当前: {fmt(cur)}")

        if healthy and (not OPT or hr >= 8.0):
            health[it["key"]] = {"t": now, "s": "ok"}
            ok_n += 1
            continue

        cands = candidates(name, year, it.get("season", 1))
        need = len(it["files"])
        pool = [c for c in cands if len(c["eps"]) >= need and c["eps"][0][1] != cur_url]
        if pool:
            nmax = max(len(c["eps"]) for c in pool)
            pool = [c for c in pool if len(c["eps"]) == nmax]

        best, best_s, best_key = None, None, None
        for c in pool:
            s = probe.score(c["eps"][0][1])
            log(f"  候选 {c['label']}: {fmt(s)}")
            if not s:
                continue
            k = (min(s["headroom"], 8.0), -s["ttfb"])
            if best_key is None or k > best_key:
                best, best_s, best_key = c, s, k

        if best is None:
            switch = False
        elif cur is None:
            switch = True
        else:
            switch = best_s["headroom"] > hr and best_s["headroom"] >= min(hr * GAIN, 8.0)

        if switch:
            if DRY:
                log(f"  [dry] 将切换到 {best['label']}")
            else:
                write_back(it, name, best["eps"])
                log(f"  已切换到 {best['label']}")
            fixed.append(f"{it['label']} → {best['label']} ({best_s['mbps']:.1f}Mbps)")
            health[it["key"]] = {"t": now, "s": "fixed"}
        else:
            if not healthy:
                state = "dead" if cur is None else "slow"
                log("  没有更好的线路,保持不变")
                unfixed.append(f"{it['label']} ({'失效' if state == 'dead' else '偏慢'})")
                health[it["key"]] = {"t": now, "s": state}
            else:
                health[it["key"]] = {"t": now, "s": "ok"}
                ok_n += 1

    if not DRY:
        json.dump(health, open(HEALTH, "w", encoding="utf-8"), ensure_ascii=False)
        if fixed:
            refresh_jellyfin()

    log(f"完成:正常 {ok_n},已切换 {len(fixed)},无法修复 {len(unfixed)}")
    if (fixed or unfixed) and not DRY:
        msg = f"线路复测完成:正常 {ok_n},已切换 {len(fixed)},无法修复 {len(unfixed)}"
        if fixed:
            msg += "\n\n已切换:\n" + "\n".join(fixed)
        if unfixed:
            msg += "\n\n无法修复(没有更好的线路):\n" + "\n".join(unfixed)
        notify(msg)


main()
EOF
chmod +x /opt/m3u/recheck.py
python3 -m py_compile /opt/m3u/recheck.py && echo "recheck.py 语法检查通过"
```

**用法**

```bash
python3 /opt/m3u/recheck.py --dry --force            # 只读检查整个库,不写文件
python3 /opt/m3u/recheck.py --force --only 片名       # 只检查名字里含"片名"的
python3 /opt/m3u/recheck.py --optimize               # 连"健康但不够快"的也去找更快线路
```

**验证"失效自动修复"**(把测试片的 STRM 故意改坏,看它能否修回来):

```bash
F="/media/movies/某某片 (2026)/某某片 (2026).strm"
cp "$F" /tmp/strm.backup
echo "https://invalid.example.com/index.m3u8" > "$F"
python3 /opt/m3u/recheck.py --force --only 某某片
cat "$F"                      # 应该已变回真实地址
```

> **能不能修复取决于有没有备选线路**。`cms_apis` 里只有一个源、且该源对这部片只有一条 m3u8 线路时,失效只能汇报"无法修复"。多配几个源。

### 6.8 辅助工具(可选)

**`add_list.py`** —— 给 `auto.py` 添加榜单地址,自动规范成本机地址、`start` 归零、去重(地址要用引号包起来,因为里面有 `&`):

```bash
cat > /opt/m3u/add_list.py <<'EOF'
#!/usr/bin/env python3
import sys, json, urllib.parse

P = "/opt/m3u/config.json"
c = json.load(open(P, encoding="utf-8"))


def norm(u):
    s = urllib.parse.urlparse(u)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(s.query, keep_blank_values=True) if k != "start"]
    q.append(("start", "0"))
    return urllib.parse.urlunparse(s._replace(
        scheme="http", netloc="127.0.0.1:3000", query=urllib.parse.urlencode(q)))


cur, seen = [], set()
for u in c.get("list_urls", []) + [a for a in sys.argv[1:] if not a.startswith("--")]:
    n = norm(u)
    if n not in seen:
        seen.add(n)
        cur.append(n)
if "--show" not in sys.argv:
    c["list_urls"] = cur
    json.dump(c, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"当前共 {len(cur)} 个分类:")
for u in cur:
    print(" -", urllib.parse.unquote(u))
EOF
```

用法:`python3 /opt/m3u/add_list.py "http://192.168.1.110:3000/api/douban/categories?kind=movie&category=...&start=0&proxyType=auto"`

**`migrate_seasons.py`** —— 把旧版本生成的"某剧 第二季"独立目录并入主剧集的 `Season 02`。默认预览,加 `--apply` 才执行。**迁移后路径变了,播放进度会丢。**

```bash
cat > /opt/m3u/migrate_seasons.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys
sys.path.insert(0, "/opt/m3u")
import seasons

APPLY = "--apply" in sys.argv
ROOT = seasons.TV_ROOT
moved = 0
for d in sorted(os.listdir(ROOT)):
    src_root = f"{ROOT}/{d}"
    if not os.path.isdir(src_root):
        continue
    fbase, fyear = seasons.split_folder(d)
    base, season, explicit = seasons.parse_title(fbase)
    if not base or not (explicit or season > 1):
        continue
    target = seasons.find_series_dir(base, fyear, season)
    if target == d:
        continue
    for sd in sorted(os.listdir(src_root)):
        sp = f"{src_root}/{sd}"
        if not os.path.isdir(sp):
            continue
        files = sorted(f for f in os.listdir(sp) if f.endswith(".strm"))
        if not files:
            continue
        dst = f"{ROOT}/{target}/Season {season:02d}"
        if os.path.isdir(dst) and any(f.endswith(".strm") for f in os.listdir(dst)):
            print(f"跳过(目标已有文件): {d}/{sd} -> {target}/Season {season:02d}")
            continue
        print(f"{'移动' if APPLY else '[预览]'}: {d}/{sd} ({len(files)}集) -> {target}/Season {season:02d}")
        moved += 1
        if not APPLY:
            continue
        os.makedirs(dst, exist_ok=True)
        for i, f in enumerate(files, 1):
            m = re.search(r"S\d+E(\d+)", f)
            ep = int(m.group(1)) if m else i
            url = open(f"{sp}/{f}", encoding="utf-8").read()
            open(f"{dst}/{base} S{season:02d}E{ep:02d}.strm", "w", encoding="utf-8").write(url)
            os.remove(f"{sp}/{f}")
        try:
            os.rmdir(sp)
        except OSError:
            pass
    if APPLY:
        try:
            os.rmdir(src_root)
        except OSError:
            pass
print(f"共 {moved} 个季目录" + ("已迁移" if APPLY else "待迁移(加 --apply 执行)"))
EOF
```

**`prune_old.py`** —— 清理库里超过 20 年的老片(只删小小的 STRM 目录,随时可重新生成)。默认预览;多季剧集会保留,避免误删较新的季。

```bash
cat > /opt/m3u/prune_old.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, shutil, datetime

APPLY = "--apply" in sys.argv
limit = datetime.date.today().year - 20
print(f"清理 {limit} 年以前的作品" + ("" if APPLY else "(预览,加 --apply 才会删除)"))
n = 0
for root, tv in (("/media/movies", False), ("/media/tv", True)):
    if not os.path.isdir(root):
        continue
    for d in sorted(os.listdir(root)):
        m = re.match(r"^(.*?)\s*\((\d{4})\)$", d)
        if not m or int(m.group(2)) >= limit:
            continue
        path = f"{root}/{d}"
        if tv:
            seasons = [x for x in os.listdir(path) if x.startswith("Season ")]
            if len(seasons) > 1:
                print(f"保留(多季,可能含新季): {d} {sorted(seasons)}")
                continue
        print(f"{'删除' if APPLY else '[预览]'}: {path}")
        n += 1
        if APPLY:
            shutil.rmtree(path)
print(f"共 {n} 个目录" + ("已删除" if APPLY else "待删除"))
EOF
```

---

### 6.9 `stats.py` — 每周统计

每周日发一条汇总到 Telegram,内容:

- **库存**:电影数、剧集数(含总季数、总集数)
- **本周新增**:和上周快照对比得出新电影、新剧集、有新集/新季的剧集(各列出前几条)
- **批量抓取进度**:已补完的分类数 / 配置的分类总数,累计入库数
- **线路复测**:正常 / 已换线路 / 偏慢 / 失效的数量,并列出偏慢和失效的片
- **磁盘剩余**:低于 3 GB 时提示(Jellyfin 12 要求数据盘至少 2 GiB 可用)
- **服务状态**:Jellyfin、DecoTV 是否响应

"本周新增"靠快照文件 `stats_snapshot.json` 对比。**只有正式发送(cron)时才更新快照**;手动加 `--no-send` 或用机器人 `/stats` 查看,不会改动快照。第一次运行只记录基线,下周起才有"新增"。

```bash
cat > /opt/m3u/stats.py <<'EOF'
#!/usr/bin/env python3
import os, re, sys, json, time, shutil, datetime, urllib.request

CONFIG = "/opt/m3u/config.json"
SNAP = "/opt/m3u/stats_snapshot.json"
CRAWL_STATE = "/opt/m3u/crawl_state.json"
HEALTH = "/opt/m3u/health.json"
MOVIES = "/media/movies"
TV = "/media/tv"
NO_SEND = "--no-send" in sys.argv     # 只打印,不发送,也不更新每周快照

cfg = json.load(open(CONFIG, encoding="utf-8"))


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def has_strm(p):
    try:
        return any(f.endswith(".strm") for f in os.listdir(p))
    except Exception:
        return False


def scan_library():
    movies, tv = [], {}
    if os.path.isdir(MOVIES):
        for d in sorted(os.listdir(MOVIES)):
            if has_strm(f"{MOVIES}/{d}"):
                movies.append(d)
    if os.path.isdir(TV):
        for d in sorted(os.listdir(TV)):
            sd = f"{TV}/{d}"
            if not os.path.isdir(sd):
                continue
            n_seasons, n_eps = 0, 0
            for s in os.listdir(sd):
                p = f"{sd}/{s}"
                if re.match(r"^Season \d+$", s) and os.path.isdir(p):
                    n = sum(1 for f in os.listdir(p) if f.endswith(".strm"))
                    if n:
                        n_seasons += 1
                        n_eps += n
            if n_seasons:
                tv[d] = {"seasons": n_seasons, "eps": n_eps}
    return movies, tv


def http_ok(url, want=None):
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            body = r.read(200).decode("utf-8", "ignore")
            return r.status < 500 and (want is None or want in body)
    except Exception:
        return False


def short(key):
    key = re.sub(r"^(movies|tv)/", "", key)
    return key.replace("/Season ", " 第").replace(" 第0", " 第") + ("季" if "/Season " in key or "Season" in key else "")


def build():
    now = datetime.datetime.now()
    movies, tv = scan_library()
    prev = load(SNAP, None)
    lines = [f"📊 影视库周报 {now:%Y-%m-%d}", ""]

    total_seasons = sum(v["seasons"] for v in tv.values())
    total_eps = sum(v["eps"] for v in tv.values())
    lines.append(f"库存:电影 {len(movies)} 部;剧集 {len(tv)} 部({total_seasons} 季,{total_eps} 集)")

    if prev is None:
        lines.append("本周新增:首次统计,已记录基线,下周起显示每周变化")
    else:
        new_movies = [m for m in movies if m not in set(prev.get("movies", []))]
        pt = prev.get("tv", {})
        new_series = [d for d in tv if d not in pt]
        grown = [(d, tv[d]["eps"] - pt[d]["eps"]) for d in tv
                 if d in pt and tv[d]["eps"] > pt[d]["eps"]]
        lines.append(f"本周新增:电影 {len(new_movies)} 部;新剧集 {len(new_series)} 部;"
                     f"有新集/新季的剧集 {len(grown)} 部")
        shown = ([f"  + {m}" for m in new_movies[:8]]
                 + [f"  + {d}" for d in new_series[:8]]
                 + [f"  ↑ {d} (+{n}集)" for d, n in grown[:5]])
        lines += shown
        rest = len(new_movies) + len(new_series) + len(grown) - len(shown)
        if rest > 0:
            lines.append(f"  ……另有 {rest} 项未列出")

    lines.append("")
    cc = cfg.get("crawl", {})
    total_types = sum(len(s.get("types", {})) for s in cc.get("sources", []))
    st = load(CRAWL_STATE, {})
    if st:
        done = sum(1 for v in st.values() if v.get("done"))
        ok = sum(1 for v in st.values() for x in v.get("seen", {}).values() if x > 0)
        lines.append(f"批量抓取:{done}/{total_types} 个分类已补完,累计入库 {ok} 部")
    else:
        lines.append("批量抓取:还没有运行记录")

    hl = load(HEALTH, {})
    if hl:
        cnt = {}
        for v in hl.values():
            cnt[v.get("s", "?")] = cnt.get(v.get("s", "?"), 0) + 1
        lines.append(f"线路复测:已检查 {len(hl)} 项,正常 {cnt.get('ok', 0)},"
                     f"已换线路 {cnt.get('fixed', 0)},偏慢 {cnt.get('slow', 0)},失效 {cnt.get('dead', 0)}")
        bad = [short(k) for k, v in hl.items() if v.get("s") in ("dead", "slow")]
        for b in bad[:10]:
            lines.append(f"  ! {b}")
        if len(bad) > 10:
            lines.append(f"  ……另有 {len(bad) - 10} 项")
    else:
        lines.append("线路复测:还没有运行记录")

    lines.append("")
    du = shutil.disk_usage("/")
    free_gb = du.free / 1024 ** 3
    warn = "  ⚠ 空间偏低,Jellyfin 要求至少 2GiB,建议清理或扩容" if free_gb < 3 else ""
    lines.append(f"磁盘:已用 {du.used / du.total * 100:.0f}%,剩余 {free_gb:.1f}GB{warn}")
    jf_ok = http_ok(cfg.get("jellyfin", {}).get("url", "http://127.0.0.1:8096").rstrip("/") + "/health")
    dt_ok = http_ok("http://127.0.0.1:3000/")
    lines.append(f"服务:Jellyfin {'正常' if jf_ok else '❌ 无响应'};DecoTV {'正常' if dt_ok else '❌ 无响应'}")
    return "\n".join(lines), movies, tv


def send(text):
    tgc = cfg.get("telegram") or {}
    if not tgc.get("token"):
        print("未配置 Telegram,仅打印")
        return
    for chat in tgc.get("allowed_chat_ids", []):
        try:
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{tgc['token']}/sendMessage",
                data=json.dumps({"chat_id": chat, "text": text[:4000]}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            print("Telegram 发送失败:", e)


def main():
    text, movies, tv = build()
    print(text)
    if NO_SEND:
        return
    send(text)
    json.dump({"t": time.time(), "movies": movies, "tv": tv},
              open(SNAP, "w", encoding="utf-8"), ensure_ascii=False)


main()
EOF
chmod +x /opt/m3u/stats.py
python3 -m py_compile /opt/m3u/stats.py && echo "stats.py 语法检查通过"
```

**用法**

```bash
python3 /opt/m3u/stats.py --no-send    # 只打印预览,不发送,不改快照
python3 /opt/m3u/stats.py              # 发送到 Telegram,并更新快照(cron 用的就是这条)
```

> **首次建议手动跑一次 `python3 /opt/m3u/stats.py`**(不带参数),让它保存基线快照,这样第一个周日的周报就能显示"本周新增"。

---

## 7. 联调验证(按顺序做,每步通过再继续)

| 步骤 | 命令 / 操作 | 通过标准 |
|---|---|---|
| 1. 配置格式 | `python3 -m json.tool /opt/m3u/config.json >/dev/null && echo OK` | 输出 OK |
| 2. 所有脚本语法 | `python3 -m py_compile /opt/m3u/*.py && echo OK` | 输出 OK |
| 3. DecoTV 登录接口 | 见下方 | 响应头里有 `set-cookie: auth=…` |
| 4. 源可访问 | `python3 /opt/m3u/crawl.py --types` | 列出分类和数量(几十秒) |
| 5. 试运行抓取 | `python3 /opt/m3u/crawl.py --dry --new 40 --pages 30` | 有"将加入";电影类显示"电影 1个",剧集类显示"剧集 N个" |
| 6. 小批量真实入库 | `python3 /opt/m3u/crawl.py --new 20 --pages 20 --type 6` | `/media/movies` 下出现目录和 `.strm` |
| 7. Jellyfin 扫描 | 控制台 → 媒体库 → 扫描所有媒体库,**记下耗时** | 电影/剧集出现 |
| 8. 网页端播放 | 点开一部电影、一集剧集 | 能播(剧集依赖第 4.6 的包装脚本) |
| 9. VidHub | 连接 Jellyfin(服务器类型选 Jellyfin,地址 `http://192.168.1.110:8096`) | 能看到库、能播;**Apple TV 上单独再验证一次** |
| 10. 手动添加 | `addvod "片名"` | 输出"已生成"和所选线路 |
| 11. 复测 | `python3 /opt/m3u/recheck.py --dry --force` | 每部片输出当前线路测速 |
| 12. 机器人 | 手机给机器人发片名 | 出现候选按钮,点击后回复"已加入" |
| 13. 统计 | 机器人发 `/stats`,或 `python3 /opt/m3u/stats.py --no-send` | 出现周报内容;Jellyfin、DecoTV 显示"正常" |

**第 3 步:登录接口测试**

```bash
curl -i -m 15 -X POST "http://127.0.0.1:3000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"script","password":"你的密码"}'
```

| 结果 | 含义 |
|---|---|
| 200 + `set-cookie: auth=…` | 成功 |
| 400 | 请求体字段名不对。浏览器 F12 → 网络,登录一次,看 `login` 请求的 Payload 字段名,按实际修改脚本里 `get_cookie()` 的 `json.dumps({...})` |
| 401 | 用户名或密码错 |
| 404 | 路径不对,F12 里确认真实地址 |

> ⚠ `/api/login` 是文档里标注为"推断"的路径,请求体的字段名也没有在文档里确认。**如果 DecoTV 的转发接口(`/api/proxy/cms`)在你的部署里不需要登录,就把 `config.json` 的 `login` 整块删掉**。

---

## 8. Telegram 机器人

### 8.1 创建机器人并取得 chat id

1. 手机 Telegram 搜 **@BotFather** → 发 `/newbot` → 按提示起名 → 得到 **Token**(形如 `123456789:AAxxx…`)
2. 打开你**刚创建的那个机器人**(不是 BotFather),点 **Start**,再随便发一句话
3. 在容器里(用 `read -s` 输入,避免 Token 留在终端历史里):

```bash
read -s -p "粘贴 Token 后回车: " TOKEN; echo
curl -s -m 10 "https://api.telegram.org/bot${TOKEN}/getMe"          # 应返回 "ok":true
curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates"           # 找 "chat":{"id": 后面的数字
```

`getUpdates` 返回空数组 `[]` 的常见原因:没给**自己创建的机器人**发过消息;机器人配过 webhook(用 `getWebhookInfo` 检查,有值就 `deleteWebhook`)。查不到也可以搜 **@userinfobot**,它会直接回复你的 id。

### 8.2 写入配置

```bash
export TOKEN
CHAT_ID=123456789 python3 - <<'PY'
import json, os
p = "/opt/m3u/config.json"
c = json.load(open(p, encoding="utf-8"))
c["telegram"] = {
    "token": os.environ["TOKEN"],
    "allowed_chat_ids": [int(os.environ["CHAT_ID"])]
}
json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("已写入")
PY
unset TOKEN
chmod 600 /opt/m3u/config.json
```

(把 `123456789` 换成你的 chat id。)

### 8.3 先前台试跑

```bash
python3 /opt/m3u/bot.py
```

看到"机器人启动,授权用户: [你的 id]"后,手机发一个片名,点候选按钮,确认回复"已加入"。`Ctrl+C` 停止。

### 8.4 做成开机自启服务

```bash
cat > /etc/systemd/system/tgbot.service <<'EOF'
[Unit]
Description=Telegram VOD bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/m3u/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now tgbot
systemctl status tgbot --no-pager
journalctl -u tgbot -f          # 看日志
```

**容器连不上 Telegram 需要代理时:**

```bash
sed -i '/^\[Service\]/a Environment=HTTPS_PROXY=http://代理IP:端口' /etc/systemd/system/tgbot.service
systemctl daemon-reload && systemctl restart tgbot
```

| 现象 | 原因 |
|---|---|
| 机器人没反应,日志"忽略未授权用户" | `allowed_chat_ids` 与你的 chat id 不一致 |
| 日志出现 409 Conflict | 同一个 Token 有两个程序在拉消息(`ps aux | grep bot.py`,或前台试跑和服务同时在运行) |
| `getUpdates 失败` | 网络连不上 Telegram,或 Token 无效 |

---

## 9. 定时任务

```bash
cat > /etc/cron.d/crawl-strm <<'EOF'
0 2 * * * root /usr/bin/flock /var/lock/strm.lock /usr/bin/timeout 3h /usr/bin/python3 /opt/m3u/crawl.py >> /var/log/crawl-strm.log 2>&1
EOF

cat > /etc/cron.d/recheck-strm <<'EOF'
30 4 * * * root /usr/bin/flock /var/lock/strm.lock /usr/bin/timeout 2h /usr/bin/python3 /opt/m3u/recheck.py >> /var/log/recheck-strm.log 2>&1
EOF

cat > /etc/cron.d/stats-strm <<'EOF'
0 9 * * 0 root /usr/bin/python3 /opt/m3u/stats.py >> /var/log/stats-strm.log 2>&1
EOF
chmod 644 /etc/cron.d/crawl-strm /etc/cron.d/recheck-strm /etc/cron.d/stats-strm
timedatectl            # 确认时区是你想要的;不对用 timedatectl set-timezone 区域/城市
```

| 时间 | 任务 | 说明 |
|---|---|---|
| 02:00 | `crawl.py` | 批量补库 / 增量更新 |
| 04:30 | `recheck.py` | 复测老片线路 |
| 05:00 | 清理转码缓存(第 4.7 节) | |
| 周日 05:30 | 清理 Jellyfin 旧日志 | |
| 周日 09:00 | `stats.py` 每周统计发到 Telegram | |

- `flock` 保证 crawl 和 recheck 不会同时运行;`timeout` 防止异常时无限挂着(`crawl.py` 每翻 10 页保存一次进度,被超时终止也不会丢)
- `auto.py` 默认**不放进 cron**(与 `crawl.py` 重复)。想用就加一条,同样套 `flock`
- 看运行情况:`tail -50 /var/log/crawl-strm.log`、`tail -50 /var/log/recheck-strm.log`

### 每晚入库量怎么定

每个分类每晚最多 `per_type_new` 部,全部加起来最多 `max_new_per_run` 部,先到先停,每天轮换起始分类。**补库期间观察三件事再决定要不要放量**:

1. Jellyfin 扫描耗时(尤其集数多的综艺;首次扫描要对每一集探测远程流)
2. 源站有没有开始报错或限流
3. 磁盘占用增长

起步建议每晚 30 部左右(`crawl.max_new_per_run`,本手册默认值)。

> 注意:总量 30、每分类 `per_type_new` 15 时,一晚只会覆盖两三个分类。想让补库在各分类之间分得更均匀,可以把 `per_type_new` 调到 5 左右:
> ```bash
> python3 - <<'PY'
> import json
> p = "/opt/m3u/config.json"
> c = json.load(open(p, encoding="utf-8"))
> c["crawl"]["per_type_new"] = 5
> json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
> PY
> ```

---

## 10. 日常使用

| 想做什么 | 怎么做 |
|---|---|
| 加一部片 | 手机给机器人发片名,点候选 |
| 加某一季 | 发 `某某剧 第二季` |
| 命令行加片 | `addvod "片名"` |
| 立刻补库一批 | 机器人发 `/crawl`,或 `python3 /opt/m3u/crawl.py --new 30` |
| 看补库进度 | `python3 /opt/m3u/crawl.py --status` |
| 看库里有多少 | 机器人发 `/stats`,或 `find /media -name "*.strm" \| wc -l` |
| 某部片突然播不了 | 重新 `addvod "片名"`(覆盖 STRM 并重新测速),或 `python3 /opt/m3u/recheck.py --force --only 片名` |
| 手动让 Jellyfin 扫描 | 机器人 `/scan`,或 Jellyfin 控制台 → 媒体库 → 扫描 |
| 删掉一部片 | `rm -rf "/media/movies/片名 (年份)"`,再扫描。**`crawl_state.json` 里记着已处理,不会自动重新加回来**;想重新加就手动 `addvod` |
| 删掉库里全部内容重来 | `rm -rf /media/movies/* /media/tv/*`;想让 crawl 重新抓,同时删 `/opt/m3u/crawl_state.json` |
| 看哪些分类 ID | `python3 /opt/m3u/crawl.py --types` |

### 目录与重复规则

- **同名同年份已存在的不覆盖**(crawl)
- crawl 对每个条目的"已处理"记录在 `crawl_state.json`;`recheck` 的记录在 `health.json`;`auto.py` 的在 `state.json`;周报的快照在 `stats_snapshot.json`。这三个文件被删就等于"失忆",脚本会重新处理
- 手动添加(`addvod`、机器人)**不套年份过滤**

---

## 11. 备份与重建

### 要备份什么

| 内容 | 路径 | 说明 |
|---|---|---|
| 脚本与配置 | `/opt/m3u/` | 含 `config.json`(有密码和 Token)、状态文件 |
| 编排文件 | `/opt/decotv/docker-compose.yml`、`/opt/jellyfin/docker-compose.yml` | |
| ffmpeg 包装脚本 | `/opt/jellyfin/wrapper/` | |
| Jellyfin 配置、用户、元数据 | `/opt/jellyfin/config/` | 海报/元数据较大;丢了可重新扫描生成,但用户、观看记录会丢 |
| DecoTV 数据 | `/opt/decotv/redis-data/` | 视频源配置、用户 |
| 定时任务 | `/etc/cron.d/*`、`/etc/systemd/system/tgbot.service` | |
| STRM 文件 | `/media/` | 很小,可以不备份(能重新生成),但备份了就不用重新补库 |

```bash
tar czf /root/strm-backup-$(date +%F).tgz \
  /opt/m3u /opt/decotv /opt/jellyfin/docker-compose.yml /opt/jellyfin/wrapper \
  /opt/jellyfin/config /etc/cron.d /etc/systemd/system/tgbot.service /media
```

> 备份文件里有 Token 和密码,**不要随便分享**。同时建议在 PVE 里对整个容器**做快照**。

### 从零重建的顺序

1. PVE 新建容器(第 1 节)→ 装基础软件和 Docker(第 2 节)
2. 部署 DecoTV + Redis(第 3 节),重新配置视频源、建 `script` 用户
3. 部署 Jellyfin + 包装脚本(第 4 节),创建媒体库、API 密钥、设置 FFmpeg 路径
4. 恢复 `/opt/m3u`(或按第 5、6 节重新生成脚本和 `config.json`)
5. 联调验证(第 7 节)
6. Telegram 机器人(第 8 节)、定时任务(第 9 节)
7. 若恢复了 `/media` 和状态文件,不需要重新补库;否则 `crawl.py` 会从头补

---

## 12. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| Jellyfin 反复重启,日志 `insufficient free space. Available: 1.8GiB, Required: 2GiB` | Jellyfin 12 要求数据目录所在磁盘至少 2 GiB 可用 | 清缓存:`rm -rf /opt/jellyfin/cache/transcodes/*`、`docker image prune -a`、`apt clean`;**治本:PVE 里把容器磁盘扩到 20 GB 以上** |
| 剧集在 Jellyfin 网页端播放报 500;日志 `not in allowed_segment_extensions`、`FFmpeg exited with code 183`、`ffprobe failed` | 源把切片伪装成 `.jpeg`,ffmpeg 默认不认 | 第 4.1 / 4.6 节的包装脚本;设置后重新扫描库让它重新探测 |
| Jellyfin 保存 FFmpeg 路径后反复重启 | 路径无效 | 第 4.6 节的回滚命令 |
| 容器 `docker ps` 显示 `Restarting` | 看原因 | `docker inspect NAME --format 'ExitCode={{.State.ExitCode}} OOMKilled={{.State.OOMKilled}}'` 和 `docker logs NAME --tail 60` |
| `curl` 源站返回 `400`、content-length 0 | 中文关键词没做 URL 编码,或没带 UA | `curl -G URL -A "Mozilla/5.0" --data-urlencode "ac=detail" --data-urlencode "wd=片名"` |
| `curl -s` 什么都不输出 | `-s` 把错误吞了 | 改用 `curl -i -m 15`,看状态码和报错 |
| 脚本报 `JSONDecodeError: Expecting value: line 1 column 1` | `config.json` 不是合法 JSON(多了一行、被保存成空文件) | `python3 -m json.tool /opt/m3u/config.json`,用本手册的 `cat > … <<'EOF'` 整体重写 |
| `crawl.py --types` 完全没有输出 | 配置里没有 `crawl.sources` | 检查 `config.json` 里的 `crawl` 块 |
| `crawl.py --types` 显示"获取分类失败" | 经 DecoTV 转发这条路不通(登录或转发问题) | 先试 `curl -i` 直连源站,通的话把 `via_decotv` 改成 `false` |
| 分类的"父类"全是 `None` | 这个源没提供父子分类 | 在 `config.json` 里手动指定每个分类是 `movie` 还是 `tv` |
| 电影被放进了 `/media/tv` | 分类被设成 `tv`,或条目带多个 m3u8/带"第N季" | 检查 `types` 配置;零星的手动 `mv` 即可 |
| 登录接口 400/404 | 字段名或路径不对 | 浏览器 F12 抓登录请求,按实际修改 `get_cookie()` |
| Jellyfin 里电视剧只显示第二季 | 源里只有第二季,正常 | — |
| Jellyfin 里同一部剧显示成两部 | 两个文件夹年份对不上 | 把文件夹名改成同一个年份,再扫描 |
| 海报或简介是空的 | TMDB 没收录(新片、冷门片、综艺) | 不影响播放;可在 Jellyfin 里对该条目"识别"手动匹配 |
| 剧集集标题对不上、集数异常多 | 综艺源把上/下/加更/花絮都当一集 | 靠 `max_eps` 限制首次入库;无法完美解决 |
| 失效的线路没有被换掉 | 没有备选线路 | `cms_apis` 多配几个源 |
| Telegram 机器人没反应 | 见 8.4 的表 | — |
| 机器人回复"未知命令" | 命令名拼错,或 `bot.py` 是旧版本没有该命令 | 发 `/help` 看当前支持的命令;统计命令是 `/stats`(也接受 `/status`);旧版本按第 6.9 节给 `bot.py` 补上 `/stats` 分支后 `systemctl restart tgbot` |
| cron 任务没跑 | 没装 cron / 没启用 | `apt install -y cron && systemctl enable --now cron`;`ls /etc/cron.d`;文件名里不能有点号 |
| `/var/log/crawl-strm.log` 不存在 | 定时任务还没运行过(手动运行的输出直接打印在屏幕上) | 手动跑并写日志:`python3 /opt/m3u/crawl.py 2>&1 \| tee -a /var/log/crawl-strm.log` |

---

## 13. 安全注意

- **`config.json` 含 DecoTV 密码、Jellyfin API 密钥、Telegram Token**,保持 `chmod 600`
- 脚本使用 DecoTV 的**普通账号**(`script`),不要用 admin
- Token / Cookie / API 密钥**不要贴到聊天或截图里**。贴过的立刻作废重发:Telegram 在 @BotFather 发 `/revoke`;DecoTV 改 `PASSWORD` 后 `docker compose up -d`;Jellyfin API 密钥在控制台里删除重建
- 输入密钥用 `read -s`,用完 `unset`;终端历史里留有密码时:`history -c && history -w`
- `allowed_chat_ids` **必须填**,机器人能往你的媒体库写文件
- 3000(DecoTV)和 8096(Jellyfin)端口**不要直接暴露到公网**。需要远程访问,用 VPN / Tailscale,或反向代理 + HTTPS
- 内容和版权:本手册只是把"源站公开接口里的播放地址"整理成播放列表,不托管任何内容。使用哪些源、观看什么内容,请遵守所在地法律

---

## 14. 已知限制与待确认事项

**限制**

1. **多季**:靠名字里的"第N季 / Season N / S2"识别;一个条目里合并了多季、只有数字后缀(如"某剧2")、或季号对不上的,无法自动拆分
2. **综艺、动漫**命名不规范,TMDB 匹配率低,Jellyfin 里常缺海报和简介,但不影响播放
3. **线路会失效**:STRM 里存的是源站当前地址,源站换域名/清资源后会失效,靠 `recheck.py` 发现并修,前提是有备选线路
4. **测速是一次采样**,不代表晚高峰表现,而且测的是"这台容器到源站"的速度
5. **网页端播放需要转码**,容器 CPU 吃满是正常的;日常用 VidHub 直连观看更舒服
6. 单个条目只能选一条线路写入 STRM,不会自动随网络状况实时切换
7. 首次扫描大量 STRM 会比较慢,因为 Jellyfin 要对每个远程流做探测

**未经我完全验证的点(重建时请自己核对)**

| 项 | 说明 |
|---|---|
| DecoTV 的 Redis 变量名 | 文档里确认的是 Kvrocks 的 `KVROCKS_URL`;Redis 用的 `REDIS_URL` 未核实 |
| `/api/login` 的路径与请求体字段 | 文档标注为"推断",第 7 节第 3 步用来验证 |
| ffmpeg 包装脚本在 Jellyfin 中的最终效果 | 单独用 `ffprobe` 验证过选项有效(能读出 h264 + aac);接入 Jellyfin 后网页端剧集能否播放,以第 4.6 / 第 7 节第 8 步的实际结果为准 |
| Jellyfin 是否会在读取 STRM 后缓存旧地址 | `recheck.py` 换线路后需要扫描一次;如果扫描后仍用旧地址,需要对该条目做"刷新元数据" |
| VidHub 对这类 STRM 的直连行为 | 以实际播放为准;网页端 500 不等于 VidHub 播不了 |

---

*手册结束。*
