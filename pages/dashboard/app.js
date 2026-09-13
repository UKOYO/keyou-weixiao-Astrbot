// 大魔王的星渊账本：全部数据经 AstrBot 插件页 Bridge 走 Web API，页面不直连后端。
(function () {
  "use strict";

  const el = {};
  [
    "subtitle", "hint", "reloadBtn",
    "statCalls", "statErrors", "statCost", "statLatency", "statCount", "scopeText",
    "modelBody", "toolBody", "toolSearch", "logBody", "flowFilter",
    "modelPager", "toolPager", "logPager", "errPager",
    "errBadge", "errTotal", "errLastHour", "errRate", "errTopCode", "errWindowLabel",
    "errChips", "errSearch", "errModel", "errToken", "errCode", "errHours",
    "errBody", "errFoot", "cardBox", "cardBtn", "tabs", "rangeSeg",
    "walBalance", "walUsed", "walRequests", "walAff", "walStamp", "walChips",
    "walBody", "walRate", "walLedgerSeg", "walLedgerChips", "walLedgerBody", "walLedgerFoot",
    "themeBtn",
  ].forEach((id) => { el[id] = document.getElementById(id); });

  let bridge = null;
  let toolItems = [];
  let flowItems = [];
  let errState = { items: [], by_code: [], by_model: [], by_token: [], hourly: [], summary: {} };
  let inflight = false;
  let walletLoaded = false;
  let walletState = null;
  let ledgerFilter = "all";

  const SOURCE_CHIP = { self: "chip-coral", code: "chip-yellow", checkin: "chip-green", aff: "chip-blue", other: "" };
  const SOURCE_LABEL = { self: "自己充值", code: "兑换码", checkin: "每日签到", aff: "邀请奖励", other: "其它入账" };

  // 统计窗口：yesterday 昨日全天 / today 今日全天
  let range = "today";
  const RANGE_LABEL = { yesterday: "昨日全天", today: "今日全天" };

  // 分页：每表一页 30 条，翻页才请求下一页，不在本地堆全量，省内存
  const PAGE_SIZE = 30;
  let pageState = {
    models: { page: 1, total: 0 },
    tools: { page: 1, total: 0 },
    logs: { page: 1, total: 0 },
    errors: { page: 1, total: 0 },
  };

  function withRange(params) {
    return Object.assign({ range: range }, params || {});
  }

  function resetPages() {
    Object.keys(pageState).forEach((k) => { pageState[k].page = 1; });
  }

  function pageCount(st) {
    return Math.max(1, Math.ceil((st.total || 0) / PAGE_SIZE));
  }

  // 生成分页控件：上一页 / 第 N-M 页 / 下一页，并回传点击目标页
  function pager(st, onGo) {
    const box = document.createElement("div");
    box.className = "pager";
    const totalPages = pageCount(st);
    const info = document.createElement("span");
    info.className = "pager-info";
    const from = (st.page - 1) * PAGE_SIZE + 1;
    const to = Math.min(st.total || 0, st.page * PAGE_SIZE);
    info.textContent = st.total ? ("第 " + from + "-" + to + " 条 / 共 " + st.total + " 条") : "暂无数据";
    const prev = document.createElement("button");
    prev.className = "pager-btn";
    prev.textContent = "上一页";
    prev.disabled = st.page <= 1;
    const next = document.createElement("button");
    next.className = "pager-btn";
    next.textContent = "下一页";
    next.disabled = st.page >= totalPages;
    const num = document.createElement("span");
    num.className = "pager-num";
    num.textContent = st.page + " / " + totalPages;
    prev.addEventListener("click", () => { if (st.page > 1) onGo(st.page - 1); });
    next.addEventListener("click", () => { if (st.page < totalPages) onGo(st.page + 1); });
    box.appendChild(prev);
    box.appendChild(num);
    box.appendChild(next);
    box.appendChild(info);
    return box;
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

  function fmtMoney(sym, v) {
    const n = Number(v) || 0;
    const s = sym || "¥";
    if (!n) return s + "0";
    return s + (Math.abs(n) < 1 ? n.toFixed(4) : n.toFixed(2));
  }

  function fmtQuota(n) {
    return (Number(n) || 0).toLocaleString("en-US") + " 额度";
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
      el.modelBody.appendChild(emptyRow(4, "暂无模型数据"));
      return;
    }
    items.forEach((it) => {
      el.modelBody.appendChild(row([
        { text: it.model },
        { text: it.calls + " 次", cls: "num" },
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
    el.errTotal.textContent = (s.total || 0) + " 条";
    // 窗口固定为全天，用窗口内的受影响模型数代替「近一小时」
    if (el.errWindowLabel) el.errWindowLabel.textContent = "受影响模型";
    el.errLastHour.textContent = ((data.by_model || []).length) + " 个";
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
    el.errFoot.textContent = "本页显示 " + items.length + " 条 / 共 " + (pageState.errors.total || errState.items.length) + " 条报错" +
      (errState.scope ? "（数据范围：" + errState.scope + "）" : "");
  }

  function renderWallet(w) {
    walletState = w;
    const sym = w.currency_symbol || "¥";
    el.walBalance.textContent = fmtMoney(sym, w.quota_money);
    el.walUsed.textContent = fmtMoney(sym, w.used_money);
    el.walRequests.textContent = (w.request_count || 0) + " 次";
    el.walAff.textContent = fmtMoney(sym, w.aff_money);
    el.walStamp.textContent = (w.site_name || "星渊") + "｜更新于 " + (w.updated_at || "--") +
      (w.stale_error ? "（降级中：" + w.stale_error + "）" : "");
    el.walRate.textContent = "换算：" +
      (Number(w.quota_per_unit) || 0).toLocaleString("en-US") + " 额度 = " + sym + "1";

    el.walChips.textContent = "";
    const checkinText = !w.checkin_enabled
      ? "站点没开签到"
      : (w.checked_in_today
        ? "今天已签 · 连签 " + (w.checkin_count || 0) + " 天"
        : "今天还没签");
    const groups = [
      ["账号", (w.display_name || w.username || "--") + (w.group ? " · " + w.group : "")],
      ["余额", fmtQuota(w.quota)],
      ["已用", fmtQuota(w.used_quota)],
      ["签到", checkinText],
      ["签到累计", fmtQuota(w.checkin_total_quota) + "（" + fmtMoney(sym, w.checkin_total_money) + "）"],
      ["邀请", (w.aff_count || 0) + " 人 · " + fmtMoney(sym, w.aff_money)],
    ];
    groups.forEach(([label, value]) => {
      const box = document.createElement("div");
      box.className = "chip-group";
      const t = document.createElement("span");
      t.className = "chip-label";
      t.textContent = label;
      const span = document.createElement("span");
      span.className = "chip chip-green";
      span.textContent = String(value);
      box.appendChild(t);
      box.appendChild(span);
      el.walChips.appendChild(box);
    });

    el.walBody.textContent = "";
    const recs = w.checkin_records || [];
    if (!recs.length) {
      el.walBody.appendChild(emptyRow(3, "还没有签到记录"));
      renderLedger(w);
      return;
    }
    recs.forEach((r) => {
      el.walBody.appendChild(row([
        { text: r.date || "--" },
        { text: fmtQuota(r.quota), cls: "num" },
        { text: fmtMoney(sym, r.money), cls: "num" },
      ]));
    });
    renderLedger(w);
  }

  /* 入账来源白名单：与渲染端 data-src 一一对应，未知来源一律归到 other */
  const LEDGER_SOURCES = ["self", "code", "checkin", "aff", "other"];

  function normSource(s) {
    return LEDGER_SOURCES.indexOf(s) >= 0 ? s : "other";
  }

  /* 同步「入账明细」分段：没有数据的来源直接藏掉，选中态一律以 ledgerFilter 为准 */
  function syncLedgerSeg(by) {
    if (!el.walLedgerSeg) return;
    const btns = el.walLedgerSeg.querySelectorAll(".seg-btn");
    Array.prototype.forEach.call(btns, (b) => {
      const key = b.dataset.src || b.dataset.source || "all";
      const dead = key !== "all" && !(by[key] && by[key].count);
      b.classList.toggle("hidden", dead);
      if (dead && ledgerFilter === key) ledgerFilter = "all";
    });
    Array.prototype.forEach.call(btns, (b) => {
      const key = b.dataset.src || b.dataset.source || "all";
      b.classList.toggle("active", key === ledgerFilter);
    });
  }

  function setLedgerFilter(next) {
    const key = String(next == null ? "all" : next);
    ledgerFilter = (key === "all" || LEDGER_SOURCES.indexOf(key) >= 0) ? key : "all";
    if (walletState) renderLedger(walletState);
  }

  function renderLedger(w) {
    const sym = (w && w.currency_symbol) || "¥";
    const sum = (w && w.ledger_summary) || {};
    const by = sum.by_source || {};
    const all = Array.isArray(w && w.ledger) ? w.ledger : [];

    /* 兜底一：筛选值不在白名单里，退回「全部」，避免整张表被误判成空 */
    if (ledgerFilter !== "all" && LEDGER_SOURCES.indexOf(ledgerFilter) < 0) {
      ledgerFilter = "all";
    }
    /* 兜底二：选中的来源这次一笔都没有，同样退回「全部」 */
    if (ledgerFilter !== "all" && !(by[ledgerFilter] && by[ledgerFilter].count)) {
      ledgerFilter = "all";
    }
    syncLedgerSeg(by);

    let rows = all.filter((r) => ledgerFilter === "all" || normSource(r.source) === ledgerFilter);

    /* 兜底三：筛选后一行都落不下来（数据结构异常 / 后端字段变了），
       直接退回「全部」重算，绝不给用户留一张空表 */
    if (ledgerFilter !== "all" && !rows.length) {
      /* 后端部分来源降级时会出现「汇总有数、明细为空」，同样退回全部 */
      ledgerFilter = "all";
      syncLedgerSeg(by);
      rows = all.length ? all.slice() : [];
    }

    el.walLedgerChips.textContent = "";
    const order = LEDGER_SOURCES.filter((k) => by[k] && by[k].count);
    order.forEach((k) => {
      const box = document.createElement("div");
      box.className = "chip-group";
      const lab = document.createElement("span");
      lab.className = "chip-label";
      lab.textContent = by[k].label || SOURCE_LABEL[k] || k;
      const chip = document.createElement("span");
      chip.className = "chip " + (SOURCE_CHIP[k] || "");
      chip.textContent = by[k].count + " 笔 · " + fmtMoney(sym, by[k].money) +
        (by[k].paid ? "（实付 " + fmtMoney(sym, by[k].paid) + "）" : "");
      box.appendChild(lab);
      box.appendChild(chip);
      el.walLedgerChips.appendChild(box);
    });
    if (order.length) {
      const box = document.createElement("div");
      box.className = "chip-group";
      const lab = document.createElement("span");
      lab.className = "chip-label";
      lab.textContent = "合计";
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = (sum.count || all.length) + " 笔 · 折合 " + fmtMoney(sym, sum.money) +
        (sum.paid ? " · 自己实付 " + fmtMoney(sym, sum.paid) : "");
      box.appendChild(lab);
      box.appendChild(chip);
      el.walLedgerChips.appendChild(box);
    }

    el.walLedgerBody.textContent = "";
    if (!rows.length) {
      el.walLedgerBody.appendChild(emptyRow(6, all.length ? "这个来源暂时没有入账" : "还没读到入账记录"));
    } else {
      rows.forEach((r) => {
        el.walLedgerBody.appendChild(row([
          { text: r.time || "--" },
          { text: r.source_label || SOURCE_LABEL[r.source] || r.source || "-" },
          { text: r.detail || "", cls: "detail-cell", title: r.detail || "" },
          { text: fmtQuota(r.quota), cls: "num" },
          { text: fmtMoney(sym, r.money), cls: "num" },
          { text: r.paid ? fmtMoney(sym, r.paid) : "—", cls: "num" },
        ]));
      });
    }
    el.walLedgerFoot.textContent = "显示 " + rows.length + " / " + all.length + " 条入账" +
      (w.ledger_error ? "（部分来源降级：" + w.ledger_error + "）" : "");

    /* 切分段后清掉横向滚动并强制重排，避免表体看似空白/挂起 */
    void el.walLedgerBody.offsetHeight;
    if (el.walLedgerBody.parentElement) el.walLedgerBody.parentElement.scrollLeft = 0;
  }

  async function loadWallet(force) {
    if (!force && walletLoaded) return;
    const w = await bridge.apiGet("api/wallet");
    if (w && w.status === "ok") {
      walletLoaded = true;
      renderWallet(w);
    }
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
    const data = await bridge.apiGet("api/errors", withRange({ page: pageState.errors.page, size: PAGE_SIZE }));
    errState.items = data.items || [];
    errState.by_code = data.by_code || [];
    errState.by_model = data.by_model || [];
    errState.by_token = data.by_token || [];
    errState.hourly = data.hourly || [];
    errState.scope = data.scope || "";
    errState.summary = data.summary || {};
    pageState.errors.total = data.total != null ? data.total : (data.items || []).length;
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
    renderPagerFor("errors", el.errPager, loadErrors);
  }

  async function loadCard(force) {
    if (!force && el.cardBox.querySelector("img")) return;
    const card = await bridge.apiGet("api/card", withRange());
    if (card && card.data_url) renderCardImg(card.data_url);
  }

  function pageParams(name) {
    return withRange({ page: pageState[name].page, size: PAGE_SIZE });
  }

  // 只拉当前页的表格数据，翻页时再单独取下一页
  async function loadModelsPage() {
    const r = await bridge.apiGet("api/models", pageParams("models"));
    pageState.models.total = (r && r.total) || 0;
    renderModels((r && r.items) || []);
    renderPagerFor("models", el.modelPager, loadModelsPage);
  }

  async function loadToolsPage() {
    const r = await bridge.apiGet("api/tools", pageParams("tools"));
    pageState.tools.total = (r && r.total) || 0;
    renderTools((r && r.items) || []);
    renderPagerFor("tools", el.toolPager, loadToolsPage);
  }

  async function loadLogsPage() {
    const r = await bridge.apiGet("api/logs", pageParams("logs"));
    pageState.logs.total = (r && r.total) || 0;
    flowItems = (r && r.items) || [];
    renderFlow();
    renderPagerFor("logs", el.logPager, loadLogsPage);
  }

  function renderPagerFor(name, host, reloader) {
    if (!host) return;
    host.textContent = "";
    host.appendChild(pager(pageState[name], (p) => {
      pageState[name].page = p;
      reloader().catch((e) => showHint("翻页失败：" + (e && e.message ? e.message : e)));
    }));
  }

  async function loadAll(force) {
    if (inflight) return;
    inflight = true;
    el.reloadBtn.disabled = true;
    showHint("正在拉取中转站数据……");
    resetPages();
    try {
      const ov = await bridge.apiGet("api/overview", withRange());
      renderOverview(ov);
      await Promise.all([
        loadModelsPage(),
        loadToolsPage(),
        loadLogsPage(),
      ]);
      await loadErrors();
      await loadWallet(!!force);
      await loadCard(!!force);
      showHint("");
    } catch (err) {
      showHint("读取失败：" + (err && err.message ? err.message : err));
    } finally {
      el.reloadBtn.disabled = false;
      inflight = false;
    }
  }

  // ------------------------------------------------------------------ 事件

  function setRange(next) {
    if (!RANGE_LABEL[next] || next === range) return;
    range = next;
    Array.prototype.forEach.call(el.rangeSeg.querySelectorAll(".seg-btn"), (b) => {
      b.classList.toggle("active", b.dataset.range === range);
    });
    // 两个窗口都是静止快照，不做自动轮询，避免空转刷接口
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
      ["overview", "wallet", "errors", "flow", "tools", "card"].forEach((v) => {
        const node = document.getElementById("view-" + v);
        if (node) node.classList.toggle("hidden", v !== btn.dataset.view);
      });
      if (btn.dataset.view === "card") loadCard(false).catch(() => {});
      if (btn.dataset.view === "wallet") loadWallet(false).catch(() => {});
    });
  }

  /* ---- 主题以插件设置为准：启动拉一次，切换回写一次，两边永远一致 ---- */
  async function syncThemeFromServer() {
    try {
      const b = await getBridge();
      const r = await b.apiGet("api/theme");
      const t = r && r.theme === "light" ? "light" : "dark";
      if (document.documentElement.getAttribute("data-theme") !== t) {
        document.documentElement.setAttribute("data-theme", t);
        try { localStorage.setItem("syuan-theme", t); } catch (err) {}
        if (el.themeBtn) el.themeBtn.textContent = t === "light" ? "黑夜" : "白昼";
      }
    } catch (err) {
      /* 拿不到就沿用本地缓存，不打断页面 */
    }
  }

  function syncThemeToServer(t) {
    getBridge().then((b) => {
      if (!b.apiPost) return;
      b.apiPost("api/theme", { theme: t }).catch(() => {});
    }).catch(() => {});
  }

  function bind() {
    el.reloadBtn.addEventListener("click", () => loadAll(true));
    el.rangeSeg.addEventListener("click", (e) => {
      const btn = e.target.closest(".seg-btn");
      if (btn) setRange(btn.dataset.range);
    });
    if (el.walLedgerSeg) {
      el.walLedgerSeg.addEventListener("click", (e) => {
        const btn = e.target.closest(".seg-btn");
        if (!btn) return;
        setLedgerFilter(btn.dataset.src || btn.dataset.source || "all");
      });
    }

    /* ---- 黑夜 / 白昼主题切换 ---- */
    function applyTheme(mode, persist) {
      const next = mode === "light" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      if (persist) {
        try { localStorage.setItem("syuan-theme", next); } catch (err) {}
      }
      if (el.themeBtn) {
        el.themeBtn.textContent = next === "light" ? "黑夜" : "白昼";
        el.themeBtn.title = next === "light" ? "切到黑夜模式" : "切到白昼模式";
      }
    }
    if (el.themeBtn) {
      applyTheme(document.documentElement.getAttribute("data-theme") || "dark", false);
      el.themeBtn.addEventListener("click", () => {
        const now = document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
        const next = now === "light" ? "dark" : "light";
        applyTheme(next, true);
        syncThemeToServer(next);
      });
      syncThemeFromServer();
    }
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
