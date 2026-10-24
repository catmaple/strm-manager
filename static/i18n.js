(function () {
  const translations = {
    "STRM 管理控制台": "STRM Manager Console",
    "管理现有脚本使用的 config.json": "Manage the config.json used by the existing scripts",
    "退出登录": "Log out",
    "语言": "Language",
    "服务状态": "Service Status",
    "配置无法读取：": "Unable to read configuration: ",
    "当前定时任务：": "Current scheduled task: ",
    "最近任务：": "Last task: ",
    "Telegram 机器人：": "Telegram bot: ",
    "计划任务：每日 02:00 批量抓取、每日 04:30 线路复测、每周日 09:00 统计（容器时区：": "Scheduled tasks: daily crawl at 02:00, daily source recheck at 04:30, and weekly statistics on Sunday at 09:00 (container time zone: ",
    "）。": ").",
    "配置表单": "Configuration",
    "最近操作结果": "Latest Operation Result",
    "基础配置": "Basic Settings",
    "保留字段（未识别配置）": "Preserved field (unrecognized configuration)",
    "选择视频源（来自 DecoTV）": "Select a source (from DecoTV)",
    "请选择 DecoTV 中已配置的视频源": "Select a source configured in DecoTV",
    "无法读取 DecoTV 视频源": "Unable to load DecoTV sources",
    "正在从 DecoTV 同步视频源…": "Syncing sources from DecoTV…",
    "已从 DecoTV 同步视频源。": "Sources synced from DecoTV.",
    "所选视频源尚未保存，请点击校验并保存。": "The selected source is not saved yet. Click Validate and Save.",
    "尚未配置 DecoTV 管理员凭证。请在本页下方填写管理员用户名和密码，点击“校验并保存”后刷新页面以同步视频源。": "DecoTV administrator credentials are not configured. Enter the administrator username and password below, click Validate and Save, then refresh the page to sync sources.",
    "DecoTV 管理配置中尚未添加视频源。请先登录 DecoTV 管理界面添加并启用视频源。": "No sources are configured in DecoTV yet. Sign in to the DecoTV admin interface, then add and enable sources.",
    "DecoTV 中没有可用的已启用非成人视频源。请检查来源是否已启用且 API 地址有效；禁用源和成人源不会显示。": "DecoTV has no available enabled non-adult sources. Check that a source is enabled and its API URL is valid. Disabled and adult sources are hidden.",
    "无法同步 DecoTV 视频源，请查看提示。": "Unable to sync DecoTV sources. See the message below.",
    "请确认该账号拥有 DecoTV 管理员权限。": "Verify that this account has DecoTV administrator privileges.",
    "DecoTV 登录成功但没有返回 Cookie，无法读取管理员配置。": "DecoTV login succeeded but returned no session cookie, so its admin configuration cannot be read.",
    "DecoTV 配置接口返回的内容不是有效 JSON/UTF-8。": "DecoTV returned invalid JSON or UTF-8 from its configuration endpoint.",
    "DecoTV 配置中未找到 SourceConfig": "The DecoTV configuration did not contain SourceConfig. Check the DecoTV version and administrator access.",
    "DecoTV 配置响应格式无效": "DecoTV returned an invalid configuration response.",
    "DecoTV 配置响应超过允许大小": "The DecoTV configuration response exceeded the allowed size.",
    "DecoTV URL 必须是有效的 HTTP 或 HTTPS 地址": "The DecoTV URL must be a valid HTTP or HTTPS address.",
    "DecoTV URL 不应包含用户名或密码": "Do not include a username or password in the DecoTV URL.",
    "连接 DecoTV 超时，请检查 DecoTV 地址、服务状态及容器网络。": "Connection to DecoTV timed out. Check the DecoTV URL, service status, and container network.",
    "无法连接 DecoTV，请检查配置的 DecoTV URL 和 STRM Manager 容器网络。": "Unable to connect to DecoTV. Check its configured URL and the STRM Manager container network.",
    "读取 DecoTV 配置失败，请检查 STRM Manager 日志和数据目录权限。": "Failed to read DecoTV configuration. Check STRM Manager logs and data-directory permissions.",
    "无法读取 DecoTV 视频源，请检查 DecoTV 地址和服务状态。": "Unable to load sources from DecoTV. Check the DecoTV URL and service status.",
    "等待生成预览…": "Waiting for preview…",
    "认证与服务地址": "Authentication and Service URLs",
    "DecoTV 登录 URL": "DecoTV Login URL",
    "DecoTV 登录用户名": "DecoTV Username",
    "DecoTV 登录密码": "DecoTV Password",
    "已配置，留空表示保留原值": "Configured; leave blank to keep the current value",
    "尚未配置": "Not configured",
    "抓取与复测参数": "Crawl and Recheck Settings",
    "榜单抓取每次新增上限": "Maximum new titles per chart crawl",
    "榜单抓取最大翻页数": "Maximum chart pages to scan",
    "每次运行最多查询数": "Maximum title lookups per run",
    "未找到资源重试间隔（天）": "Retry interval for missing titles (days)",
    "线路最低余量阈值": "Minimum route headroom threshold",
    "切换线路所需提升倍数": "Required improvement factor to switch routes",
    "线路复测间隔（天）": "Route recheck interval (days)",
    "每次复测最大线路数": "Maximum routes to recheck per run",
    "定时任务": "Scheduled Tasks",
    "启用定时任务总开关": "Enable scheduled tasks",
    "批量抓取启用": "Enable scheduled crawling",
    "抓取脚本": "Crawl script",
    "auto.py（默认分类）": "auto.py (default categories)",
    "crawl.py（指定源）": "crawl.py (selected sources)",
    "抓取时间（时）": "Crawl time (hour)",
    "抓取时间（分）": "Crawl time (minute)",
    "线路复测启用": "Enable source recheck",
    "复测时间（时）": "Recheck time (hour)",
    "复测时间（分）": "Recheck time (minute)",
    "周统计启用": "Enable weekly statistics",
    "周统计星期": "Weekly statistics day",
    "周一": "Monday",
    "周二": "Tuesday",
    "周三": "Wednesday",
    "周四": "Thursday",
    "周五": "Friday",
    "周六": "Saturday",
    "周日": "Sunday",
    "周统计时间（时）": "Weekly statistics time (hour)",
    "周统计时间（分）": "Weekly statistics time (minute)",
    "telegram.allowed_chat_ids（多个数字以逗号分隔）": "telegram.allowed_chat_ids (comma-separated numbers)",
    "例如：123456789，多个数字用逗号分隔": "Example: 123456789; separate multiple IDs with commas",
    "Telegram 功能说明": "Telegram bot features",
    "Telegram 机器人提供的功能：": "Telegram bot features:",
    "搜索 DecoTV 全部已启用视频源，选择版本和播放线路后添加到 Jellyfin（线路显示清晰度、速度、首包响应时间和来源）": "Search all enabled DecoTV sources, then choose a version and playback route to add to Jellyfin (routes show resolution, speed, first-byte response time, and source)",
    "同名唯一时跳过版本选择，但仍可选择播放线路": "Skips version selection when there is a unique match, but still lets you choose a playback route",
    "选择本地电影或剧集，并二次确认后连同海报、NFO 等文件一起删除": "Select a local movie or TV season and, after confirmation, delete it together with posters, NFO and other files",
    "测速并显示可用线路的速度/分辨率，点击切换播放源": "Measure available routes, show speed/resolution, and click to switch the playback source",
    "预览旧剧集季目录迁移，确认后执行文件移动": "Preview migration of legacy TV season folders; confirm to move files",
    "运行分类抓取或榜单抓取": "Run a category crawl or chart/list crawl",
    "复测线路、查看状态或通知 Jellyfin 扫描": "Recheck routes, view status, or request a Jellyfin library scan",
    "抓取源配置": "Crawl Source Settings",
    "通过 DecoTV 代理抓取（crawl.via_decotv）": "Fetch through the DecoTV proxy (crawl.via_decotv)",
    "跳过未标注年份的视频（crawl.skip_unknown_year）": "Skip videos without a year (crawl.skip_unknown_year)",
    "评分来源": "Rating source",
    "豆瓣评分": "Douban rating",
    "视频源评分": "Video source rating",
    "优先豆瓣，无豆瓣分时使用视频源评分": "Prefer Douban; use the video source rating when unavailable",
    "最低评分": "Minimum rating",
    "只抓取最近几年作品": "Only crawl titles from the last N years",
    "最低作品年份": "Earliest release year",
    "每次抓取新增上限": "Maximum new titles per crawl run",
    "每次抓取最大翻页数": "Maximum pages per crawl run",
    "每个分类新增上限": "Maximum new titles per category",
    "每个分类最大翻页数": "Maximum pages per category",
    "补库阶段最大页数（0 表示不限制）": "Maximum backfill pages (0 means unlimited)",
    "增量更新最大页数": "Maximum pages for incremental updates",
    "剧集最大集数（0 表示不限制）": "Maximum episodes per series (0 means unlimited)",
    "抓取请求间隔（秒）": "Delay between crawl requests (seconds)",
    "忽略以下类型": "Ignore the following types",
    "TC（枪版）": "TC (camrip)",
    "TS（枪版）": "TS (telesync)",
    "CAM（枪版）": "CAM (cam recording)",
    "枪版": "Camrip",
    "抢先": "Early release",
    "预告": "Trailer",
    "校验并保存": "Validate and Save",
    "测试连接": "Test Connection",
    "Dry Run": "Dry Run",
    "扫描DecoTV默认分类": "Scan DecoTV Default Categories",
    "扫描视频源分类": "Scan Video Source Categories",
    "线路复测": "Recheck Sources",
    "统计数量": "Count Items",
    "保存前自动备份最近 10 份；测试连接、Dry Run 和立即执行都不会覆盖现有配置。": "The 10 most recent backups are kept. Test Connection, Dry Run, and Run Now do not overwrite the current configuration.",
    "查看完整 config.json": "View Full config.json",
    "敏感信息以星号隐藏。": "Sensitive values are masked with asterisks.",
    "最近定时任务日志": "Recent Scheduled Task Logs",
    "详细日志持久化在 data/manager/logs/，脚本输出中的已配置密钥会脱敏。": "Detailed logs are stored in data/manager/logs/. Configured secrets are masked in script output.",
    "管理网页默认仅绑定到本机 127.0.0.1。远程管理建议使用 SSH 隧道或 VPN。": "The management page binds to 127.0.0.1 by default. Use an SSH tunnel or VPN for remote access.",
    "STRM 管理登录": "STRM Manager Login",
    "STRM 管理": "STRM Manager",
    "使用部署时设置的管理员账号登录。": "Sign in with the administrator account configured during deployment.",
    "用户名": "Username",
    "密码": "Password",
    "登录": "Sign In",
    "未运行": "Not running",
    "尚无定时任务": "No scheduled task yet",
    "无": "None",
    "正常": "Healthy",
    "未配置/未运行": "Not configured / not running",
    "暂无日志。": "No logs yet.",
    "源分类：": "Source categories:",
    "未知": "Unknown",
    "正在生成预览…": "Generating preview…",
    "未配置 CMS API，预览为空。": "No CMS API configured; preview is empty.",
    "生成预览失败。": "Failed to generate preview.",
    "已生成预览。": "Preview generated.",
    "生成预览失败，请稍后再试。": "Preview generation failed. Please try again later.",
    "登录表单已过期，请重试。": "The login form expired. Please try again.",
    "用户名或密码不正确。": "Incorrect username or password.",
    "配置已校验并保存。定时任务和脚本下次运行时会读取新配置；Telegram 机器人会自动重启。": "Configuration validated and saved. Scheduled tasks and scripts will use it on their next run; the Telegram bot will restart automatically.",
    "配置未保存：": "Configuration was not saved: ",
    "配置保存失败：": "Failed to save configuration: ",
    "Dry Run 完成（退出码 ": "Dry Run completed (exit code ",
    "已启动任务：": "Task started: ",
    "DecoTV：": "DecoTV: ",
    "DecoTV 登录：": "DecoTV login: ",
    "Jellyfin：": "Jellyfin: ",
    "Redis：": "Redis: ",
    "正常（HTTP ": "Healthy (HTTP ",
    "正常（PONG）": "Healthy (PONG)",
    "不可用：": "Unavailable: ",
    "未从 CMS 接口查询到有效分类，请检查 cms_apis 或接口返回格式。": "No valid categories were found. Check cms_apis and the API response format.",
    "已生成 ": "Generated ",
    " 个源分类。": " source categories.",
    "移除": "Remove"
  };
  const originalText = new WeakMap();

  function translateText(value) {
    if (translations[value] !== undefined) return translations[value];
    if (value === "、") return ", ";
    if (value.startsWith("：") && translations[value.slice(1)] !== undefined) {
      return ": " + translations[value.slice(1)];
    }
    if (value.startsWith("DecoTV 管理员登录失败：HTTP ")) {
      return value
        .replace("DecoTV 管理员登录失败：HTTP ", "DecoTV administrator login failed: HTTP ")
        .replace("。请检查管理员账号和密码。", ". Check the administrator username and password.");
    }
    if (value.startsWith("DecoTV 管理员登录后读取配置失败：HTTP ")) {
      return value
        .replace("DecoTV 管理员登录后读取配置失败：HTTP ", "Signed in to DecoTV, but failed to read its configuration: HTTP ")
        .replace("。", ". ");
    }
    if (value.startsWith("已生成 ") && value.endsWith(" 个源分类。")) {
      return value.replace("已生成 ", "Generated ").replace(" 个源分类。", " source categories.");
    }
    if (value.startsWith("正常（HTTP ") && value.endsWith("）")) {
      return value.replace("正常（HTTP ", "Healthy (HTTP ").replace(/）$/, ")");
    }
    if (value.startsWith("配置无法读取：")) {
      return value.replace("配置无法读取：", "Unable to read configuration: ");
    }
    if (value.startsWith("配置未保存：")) {
      return value.replace("配置未保存：", "Configuration was not saved: ");
    }
    if (value.startsWith("配置保存失败：")) {
      return value.replace("配置保存失败：", "Failed to save configuration: ");
    }
    if (value.startsWith("已启动任务：")) {
      return value.replace("已启动任务：", "Task started: ");
    }
    return value;
  }

  function applyLanguage(language) {
    const useEnglish = language === "en";
    document.documentElement.lang = useEnglish ? "en" : "zh-CN";
    document.title = translateText(document.querySelector("title").textContent);

    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!parent || parent.closest("script, style, pre, textarea")) continue;
      const original = originalText.get(node) || node.nodeValue.trim();
      if (!originalText.has(node)) originalText.set(node, original);
      const translated = useEnglish ? translateText(original) : original;
      if (translated !== original || node.nodeValue.trim() !== original) {
        node.nodeValue = node.nodeValue.replace(node.nodeValue.trim(), translated);
      }
    }

    document.querySelectorAll("input[placeholder]").forEach((element) => {
      const original = element.dataset.i18nPlaceholder || element.placeholder;
      if (!element.dataset.i18nPlaceholder) element.dataset.i18nPlaceholder = original;
      element.placeholder = useEnglish ? translateText(original) : original;
    });
    document.querySelectorAll("[aria-label]").forEach((element) => {
      const original = element.dataset.i18nAriaLabel || element.getAttribute("aria-label");
      if (!element.dataset.i18nAriaLabel) element.dataset.i18nAriaLabel = original;
      element.setAttribute("aria-label", useEnglish ? translateText(original) : original);
    });
    document.querySelectorAll("[data-language-selector]").forEach((selector) => {
      selector.value = useEnglish ? "en" : "zh";
    });
    window.dispatchEvent(new CustomEvent("strm-language-change", { detail: { language: useEnglish ? "en" : "zh" } }));
  }

  window.strmTranslate = function (value) {
    return localStorage.getItem("strm-language") === "en" ? translateText(value) : value;
  };

  document.addEventListener("DOMContentLoaded", function () {
    const savedLanguage = localStorage.getItem("strm-language") || "zh";
    applyLanguage(savedLanguage);
    document.querySelectorAll("[data-language-selector]").forEach((selector) => {
      selector.addEventListener("change", function () {
        localStorage.setItem("strm-language", selector.value);
        applyLanguage(selector.value);
      });
    });
  });
})();
