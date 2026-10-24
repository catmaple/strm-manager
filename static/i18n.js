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
    "未能从 DecoTV 获取到视频源。请先到 DecoTV 设置并启用视频源，否则 STRM Manager 无法抓取到可供 Jellyfin 播放的视频。": "No video sources could be retrieved from DecoTV. Configure and enable sources in DecoTV first; otherwise, STRM Manager cannot crawl videos for Jellyfin playback.",
    "请选择 DecoTV 中已配置的视频源": "Select a source configured in DecoTV",
    "无法读取 DecoTV 视频源": "Unable to load DecoTV sources",
    "正在从 DecoTV 同步视频源…": "Syncing sources from DecoTV…",
    "已从 DecoTV 同步视频源。": "Sources synced from DecoTV.",
    "无法读取 DecoTV 视频源，请检查 DecoTV 地址和服务状态。": "Unable to load sources from DecoTV. Check the DecoTV URL and service status.",
    "DecoTV 中没有可用视频源，请先在 DecoTV 中设置视频源。": "No available sources in DecoTV. Configure sources in DecoTV first.",
    "等待生成预览…": "Waiting for preview…",
    "认证与服务地址": "Authentication and Service URLs",
    "DecoTV 登录 URL": "DecoTV Login URL",
    "DecoTV 登录用户名": "DecoTV Username",
    "DecoTV 登录密码": "DecoTV Password",
    "已配置，留空表示保留原值": "Configured; leave blank to keep the current value",
    "尚未配置": "Not configured",
    "抓取与复测参数": "Crawl and Recheck Settings",
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
    "抓取源配置": "Crawl Source Settings",
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
