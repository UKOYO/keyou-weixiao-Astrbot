// 大魔王的星渊账本：全部数据经 AstrBot 插件页 Bridge 走 Web API，页面不直连后端。
(function () {
  "use strict";

  const el = {};
  [
    "subtitle", "hint", "reloadBtn", "liveToggle", "liveInterval", "liveDot",
    "statCalls", "statErrors", "statCost", "statLatency", "statCount", "scopeText",
    "modelBody", "toolBody", "toolSearch", "logBody", "flowFilter",
    "errBadge", "errTotal", "errLastHour", "errRate", "errTopCode", "errWindowLabel",
    "errChips", "errSearch", "errModel", "errToken", "errCode", "errHours",
    "errBody", "errFoot", "cardBox", "cardBtn", "tabs", "rangeSeg",
  ].forEach((id) => { el[id] = document.getElementById(id); });

  let bridge = null;
  let toolItems = [];
  let flowItems = [];
  let errState = { items: [], by_code: [], by_model: [], by_token: [], hourly: [], summary: {} };
  let liveTimer = null;
  let inflight = false;

  // 统计窗口：live 实时近况 / yesterday 昨日全天 / today 今日全天
  let range = "live";
  const RANGE_LABEL = { live: "实时近况", yesterday: "昨日全天", today: "今日全天" };

  function withRange(params) {
    return Object.assign({ range: range }, params || {});
  }

  // ------------------------------------------------------------------ 基础

  async function getBridge() {
    const deadline = Date.now() + 8000;
    while (!window.AstrBotPluginPage && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 120));
    }
    if (!window.AstrBotPluginPage) {
      throw new Error("Bridge 未就绪，请从 AstrBot 后台插件拓展页打开本账本");
    }
    await window.AstrBotPluginPage.ready();
    return window.AstrBotPluginPage;
  }

  function showHint(text) {
    if (!text) {
      el.hint.classList.add("hidden");
      el.hint.textContent = "";
      return;
    }
    el.hint.textContent = text;
    el.hint.classList.remove("hidden");
  }

  function fmtCost(v) {
    const n = Number(v) || 0;
    if (n <= 0) return "$0";
    if (n < 0.0001) return "$" + n.toFixed(4);
    return "$" + n.toFixed(2);
  }

  function fmtTime(ts) {
    const d = new Date((Number(ts) || 0) * 1000);
    if (Number.isNaN(d.getTime()) || !Number(ts)) return "--";
    const p = (v) => String(v).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " +
      p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function fmtClock(ts) {
    const d = new Date((Number(ts) || 0) * 1000);
    if (Number.isNaN(d.getTime()) || !Number(ts)) return "--";
    const p = (v) => String(v).padStart(2, "0");
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }

  function row(cells, cls) {
    const tr = document.createElement("tr");
    if (cls) tr.className = cls;
    cells.forEach((c) => {
      const td = document.createElement("td");
      if (c && c.cls) td.className = c.cls;
      if (c && c.title) td.title = c.title;
      if (c && c.html !== undefined) td.innerHTML = c.html;
      else if (c) td.textContent = c.text != null ? c.text : "";
      tr.appendChild(td);
    });
    return tr;
  }

  function emptyRow(cols, text) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = text;
    tr.appendChild(td);
    return tr;
  }

  function fillSelect(node, values, allLabel) {
    const keep = node.value;
    node.textContent = "";
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = allLabel;
    node.appendChild(opt0);
    values.forEach((v) => {
      const o = document.createElement("option");
      o.value = v;
      o.textContent = v;
      node.appendChild(o);
    });
    if (values.indexOf(keep) !== -1) node.value = keep;
  }

  // ------------------------------------------------------------------ 渲染

  function renderOverview(ov) {
    el.statCalls.textContent = (ov.total_calls || 0) + " 次";
    el.statErrors.textContent = (ov.error_calls || 0) + " 次";
    el.statCost.textContent = fmtCost(ov.total_cost);
    el.statLatency.textContent = (ov.avg_latency || 0).toFixed(2) + " s";
    el.statCount.textContent = (ov.model_count || 0) + " / " + (ov.tool_count || 0);
    el.scopeText.textContent = "数据范围：" + (ov.scope || "--") +
      "｜窗口：" + (ov.period || RANGE_LABEL[range]) +
      (ov.fetch_error ? "（降级中：" + ov.fetch_error + "）" : "");
    el.subtitle.textContent = (ov.period || RANGE_LABEL[range]) + "｜更新于 " + ov.updated_at +
      "｜成功 " + (ov.success_calls || 0) + " · 报错 " + (ov.error_calls || 0) +
      " · 占比 " + (ov.error_rate || 0) + "%";
    setBadge(ov.error_calls || 0);
  }

  function setBadge(n) {
    const v = Number(n) || 0;
    el.errBadge.textContent = v > 999 ? "999+" : String(v);
    el.errBadge.classList.toggle("hidden", v <= 0);
  }

  function renderModels(items) {
    el.modelBody.textContent = "";
    if (!items.length) {
      el.modelBody.appendChild(emptyRow(5, "暂无模型数据"));
      return;
    }
    items.forEach((it) => {
      el.modelBody.appendChild(row([
        { text: it.model },
        { text: it.calls + " 次", cls: "num" },
        { text: it.errors ? it.errors + " 次" : "-", cls: it.errors ? "num err-text" : "num" },
        { text: (it.avg_latency || 0).toFixed(1) + " s", cls: "num" },
        { text: fmtCost(it.cost), cls: "num" },
      ]));
    });
  }

  function renderTools(items) {
    toolItems = items;
    el.toolBody.textContent = "";
    if (!items.length) {
      el.toolBody.appendChild(emptyRow(6, "本地日志暂无工具调用记录"));
      return;
    }
    items.forEach((it) => {
      el.toolBody.appendChild(row([
        { text: it.name },
        { text: it.tool, cls: "tag-id" },
        { text: it.llm, cls: "tag-id" },
        { text: it.calls + " 次", cls: "num" },
        { text: it.tokens + "", cls: "num" },
        { text: fmtCost(it.cost), cls: "num" },
      ]));
    });
  }

  function renderFlow() {
    const mode = el.flowFilter ? el.flowFilter.value : "all";
    const items = flowItems.filter((it) => mode === "all" || it.status === mode);
    el.logBody.textContent = "";
    if (!items.length) {
      el.logBody.appendChild(emptyRow(9, mode === "error" ? "这段时间没有报错，清爽得很~" : "暂无流水"));
      return;
    }
    items.forEach((it) => {
      const isErr = it.status === "error";
      el.logBody.appendChild(row([
        { text: fmtTime(it.created_at) },
        { text: isErr ? "错误" : "成功", cls: isErr ? "status-err" : "status-ok" },
        { text: it.token || "-" },
        { text: it.model },
        { text: (it.use_time || 0).toFixed(1) + " s", cls: "num" },
        { text: it.prompt_tokens + "", cls: "num" },
        { text: it.completion_tokens + "", cls: "num" },
        { text: fmtCost(it.cost), cls: "num" },
        isErr
          ? { text: it.detail || "", cls: "detail-cell", title: it.detail || "" }
          : { text: "", cls: "detail-cell" },
      ], isErr ? "tr-err" : ""));
    });
  }

  function renderErrorCards(data) {
    const s = data.summary || {};
    const isLive = range === "live";
    el.errTotal.textContent = (s.total || 0) + " 条";
    // 历史窗口下「近一小时」没有意义，改成窗口内的受影响模型数
    if (el.errWindowLabel) el.errWindowLabel.textContent = isLive ? "近一小时" : "受影响模型";
    el.errLastHour.textContent = isLive
      ? (s.last_hour || 0) + " 条"
      : ((data.by_model || []).length) + " 个";
    el.errRate.textContent = (s.error_rate || 0) + " %";
    const top = (data.by_code || [])[0];
    el.errTopCode.textContent = top ? top.name + " ×" + top.count : "无";
    setBadge(s.total || 0);
  }

  function chip(text, count, cls) {
    const span = document.createElement("span");
    span.className = "chip " + (cls || "");
    span.textContent = text + " ×" + count;
    return span;
  }

  function renderErrorChips(data) {
    el.errChips.textContent = "";
    const groups = [
      ["状态码", data.by_code || [], "chip-red"],
      ["模型", data.by_model || [], "chip-coral"],
      ["令牌", data.by_token || [], "chip-yellow"],
      ["渠道", data.by_channel || [], "chip-blue"],
    ];
    let any = false;
    groups.forEach(([label, list, cls]) => {
      if (!list.length) return;
      any = true;
      const box = document.createElement("div");
      box.className = "chip-group";
      const t = document.createElement("span");
      t.className = "chip-label";
      t.textContent = label;
      box.appendChild(t);
      list.slice(0, 5).forEach((it) => box.appendChild(chip(it.name, it.count, cls)));
      el.errChips.appendChild(box);
    });
    if (!any) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "这段时间没有报错记录，中转站很乖。";
      el.errChips.appendChild(p);
      return;
    }
    const hours = data.hourly || [];
    if (hours.some((v) => v > 0)) {
      const box = document.createElement("div");
      box.className = "chip-group";
      const t = document.createElement("span");
      t.className = "chip-label";
      t.textContent = "近24h分布";
      box.appendChild(t);
      const chart = document.createElement("span");
      chart.className = "spark";
      const max = Math.max.apply(null, hours.concat([1]));
      hours.forEach((v, i) => {
        const bar = document.createElement("i");
        bar.style.height = Math.max(2, Math.round((v / max) * 26)) + "px";
        bar.title = i + " 时：" + v + " 条";
        if (v > 0) bar.className = "on";
        chart.appendChild(bar);
      });
      box.appendChild(chart);
      el.errChips.appendChild(box);
    }
  }

  function renderErrorTable() {
    const kw = (el.errSearch.value || "").trim().toLowerCase();
    const model = el.errModel.value;
    const token = el.errToken.value;
    const code = el.errCode.value;
    const hours = Number(el.errHours.value) || 0;
    const floor = hours > 0 ? Date.now() / 1000 - hours * 3600 : 0;

    const items = errState.items.filter((it) => {
      if (model && it.model !== model) return false;
      if (token && it.token !== token) return false;
      if (code && String(it.code) !== code) return false;
      if (floor && (it.created_at || 0) < floor) return false;
      if (kw) {
        const hay = (it.detail + " " + it.kind + " " + it.model + " " + it.token).toLowerCase();
        if (hay.indexOf(kw) === -1) return false;
      }
      return true;
    });

    el.errBody.textContent = "";
    if (!items.length) {
      el.errBody.appendChild(emptyRow(8, errState.items.length ? "当前筛选条件下没有报错" : "最近的日志里没有报错，安心~"));
    } else {
      items.forEach((it) => {
        el.errBody.appendChild(row([
          { text: fmtTime(it.created_at) },
          { text: it.token },
          { text: it.model },
          { text: it.channel_name || (it.channel ? "渠道 " + it.channel : "-") },
          { text: it.code ? String(it.code) : "-", cls: "num code-cell" },
          { text: it.kind, cls: "kind-cell" },
          { text: (it.use_time || 0).toFixed(1) + " s", cls: "num" },
          { text: it.detail || "", cls: "detail-cell", title: it.detail || "" },
        ], "tr-err"));
      });
    }
    el.errFoot.textContent = "显示 " + items.length + " / " + errState.items.length + " 条报错" +
      (errState.scope ? "（数据范围：" + errState.scope + "）" : "");
  }

  function renderCardImg(dataUrl) {
    el.cardBox.textContent = "";
    const img = document.createElement("img");
    img.alt = "手账卡片";
    img.src = dataUrl;
    el.cardBox.appendChild(img);
  }

  // ------------------------------------------------------------------ 拉取

  async function loadErrors() {
    const data = await bridge.apiGet("api/errors", withRange({ limit: 500 }));
    errState.items = data.items || [];
    errState.by_code = data.by_code || [];
    errState.by_model = data.by_model || [];
    errState.by_token = data.by_token || [];
    errState.hourly = data.hourly || [];
    errState.scope = data.scope || "";
    errState.summary = data.summary || {};
    el.errChips.dataset.byChannel = JSON.stringify(data.by_channel || []);
    renderErrorCards(data);
    renderErrorChips({
      by_code: data.by_code, by_model: data.by_model,
      by_token: data.by_token, by_channel: data.by_channel, hourly: data.hourly,
    });
    fillSelect(el.errModel, (data.by_model || []).map((i) => i.name), "全部模型");
    fillSelect(el.errToken, (data.by_token || []).map((i) => i.name), "全部令牌");
    fillSelect(el.errCode, (data.by_code || []).map((i) => i.name.replace(/\D/g, "")).filter(Boolean), "全部状态码");
    renderErrorTable();
  }

  async function loadCard(force) {
    if (!force && el.cardBox.querySelector("img")) return;
    const card = await bridge.apiGet("api/card", withRange());
    if (card && card.data_url) renderCardImg(card.data_url);
  }

  async function loadAll(force) {
    if (inflight) return;
    inflight = true;
    el.reloadBtn.disabled = true;
    showHint("正在拉取中转站数据……");
    try {
      const [ov, models, tools, logs] = await Promise.all([
        bridge.apiGet("api/overview", withRange()),
        bridge.apiGet("api/models", withRange()),
        bridge.apiGet("api/tools", withRange()),
        bridge.apiGet("api/logs", withRange({ limit: 200 })),
      ]);
      renderOverview(ov);
      if (models && models.items) renderModels(models.items);
      if (tools && tools.items) renderTools(tools.items);
      flowItems = (logs && logs.items) || [];
      renderFlow();
      await loadErrors();
      await loadCard(!!force);
      showHint("");
    } catch (err) {
      showHint("读取失败：" + (err && err.message ? err.message : err));
    } finally {
      el.reloadBtn.disabled = false;
      inflight = false;
    }
  }

  async function tickLive() {
    if (inflight) return;
    inflight = true;
    el.liveDot.classList.add("on");
    try {
      const live = await bridge.apiGet("api/live", withRange());
      renderOverview({
        total_calls: live.total_calls,
        success_calls: live.success_calls,
        error_calls: live.error_calls,
        error_rate: live.error_rate,
        total_cost: live.total_cost,
        avg_latency: live.avg_latency,
        model_count: (el.statCount.textContent.split(" / ")[0]) || 0,
        tool_count: (el.statCount.textContent.split(" / ")[1]) || 0,
        scope: live.scope,
        period: live.period,
        updated_at: live.updated_at,
      });
      el.errLastHour.textContent = (live.error_last_hour || 0) + " 条";
      flowItems = (live.recent || []).map((it) => ({
        created_at: it.created_at, token: it.token, model: it.model,
        status: it.status, code: it.code, detail: it.detail,
        use_time: it.use_time, cost: it.cost,
        prompt_tokens: 0, completion_tokens: 0,
      }));
      renderFlow();
      showHint("");
    } catch (err) {
      showHint("实时刷新失败：" + (err && err.message ? err.message : err));
    } finally {
      el.liveDot.classList.remove("on");
      inflight = false;
    }
  }

  function setLive(on) {
    if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
    }
    if (!on) {
      el.liveDot.classList.remove("on");
      return;
    }
    const sec = Math.max(3, Number(el.liveInterval.value) || 10);
    liveTimer = setInterval(tickLive, sec * 1000);
    tickLive();
  }

  // ------------------------------------------------------------------ 事件

  function setRange(next) {
    if (!RANGE_LABEL[next] || next === range) return;
    range = next;
    Array.prototype.forEach.call(el.rangeSeg.querySelectorAll(".seg-btn"), (b) => {
      b.classList.toggle("active", b.dataset.range === range);
    });
    // 历史窗口是静止快照，没必要继续轮询，避免空转刷接口
    const isLive = range === "live";
    el.liveToggle.disabled = !isLive;
    if (!isLive && el.liveToggle.checked) {
      el.liveToggle.checked = false;
      setLive(false);
    }
    if (el.cardBox) el.cardBox.textContent = "";
    loadAll(true).catch(() => {});
  }

  function bindTabs() {
    el.tabs.addEventListener("click", (e) => {
      const btn = e.target.closest(".tab");
      if (!btn) return;
      Array.prototype.forEach.call(el.tabs.querySelectorAll(".tab"), (b) => {
        b.classList.toggle("active", b === btn);
      });
      ["overview", "errors", "flow", "tools", "card"].forEach((v) => {
        const node = document.getElementById("view-" + v);
        if (node) node.classList.toggle("hidden", v !== btn.dataset.view);
      });
      if (btn.dataset.view === "card") loadCard(false).catch(() => {});
    });
  }

  function bind() {
    el.reloadBtn.addEventListener("click", () => loadAll(true));
    el.rangeSeg.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (btn) setRange(btn.dataset.range);
    });
    el.liveToggle.addEventListener("change", () => setLive(el.liveToggle.checked));
    el.liveInterval.addEventListener("change", () => {
      if (el.liveToggle.checked) setLive(true);
    });
    el.cardBtn.addEventListener("click", () => loadCard(true).catch(() => {}));
    el.flowFilter.addEventListener("change", renderFlow);

    el.errSearch.addEventListener("input", renderErrorTable);
    [el.errModel, el.errToken, el.errCode, el.errHours].forEach((n) => {
      n.addEventListener("change", renderErrorTable);
    });

    if (el.toolSearch) {
      el.toolSearch.addEventListener("input", function () {
        const kw = this.value.trim().toLowerCase();
        renderTools(toolItems.filter((it) => {
          const hay = (it.name + " " + it.tool + " " + it.llm).toLowerCase();
          return !kw || hay.indexOf(kw) !== -1;
        }));
      });
    }
  }

  async function init() {
    try {
      bridge = await getBridge();
    } catch (err) {
      el.subtitle.textContent = "页面需从 AstrBot 后台打开";
      showHint(err.message);
      return;
    }
    bindTabs();
    bind();
    await loadAll(false);
  }

  init();
})();
