let customerSession = null;
let pendingConfirmationId = null;
const $ = (id) => document.getElementById(id);
const financeLabels = { order: "订单", bill: "账单", invoice: "发票", refund: "退费进度", balance: "账户余额" };
const fieldLabels = { order_id: "订单号", bill_id: "账单号", invoice_id: "发票号", refund_id: "退费单号", course_name: "课程", description: "说明", amount: "金额", balance: "余额", status: "状态", email: "发送邮箱", paid_at: "支付时间", submitted_at: "提交时间", currency: "币种" };
const recordingPrompts = ["你们有哪些课程？", "打开我的课表", "查看学习报告", "查询订单和发票", "我要请假", "帮我关闭自动续费", "创建上课提醒", "转人工客服"];
const actionRules = [
  [/课表|上课时间/, "learning", "学习中心"], [/学习报告|成绩|学习进度/, "learning", "学习中心"],
  [/订单|账单|发票|退款|退费|余额/, "finance", "财务中心"],
  [/续费|扣款|请假|缺席|补课|调课/, "services", "办事服务"], [/提醒|通知/, "reminders", "提醒中心"],
  [/人工|客服|老师回复/, "services", "办事服务"],
];
const panelNames = { overview: "首页", learning: "学习中心", finance: "财务中心", services: "办事服务", reminders: "提醒中心", assistant: "AI 客服" };

const reminderLeadLabel = document.createElement("label");
const unsupportedMonthly = $("reminder-repeat").querySelector('option[value="monthly"]');
if (unsupportedMonthly) unsupportedMonthly.remove();
if (!$("reminder-repeat").querySelector('option[value="weekdays"]')) {
  const weekdays = document.createElement("option"); weekdays.value = "weekdays"; weekdays.textContent = "工作日";
  $("reminder-repeat").append(weekdays);
}
reminderLeadLabel.textContent = "提前提醒";
const reminderLeadSelect = document.createElement("select");
reminderLeadSelect.id = "reminder-lead-time";
[[0, "到点提醒"], [30, "提前 30 分钟"], [60, "提前 1 小时"], [1440, "提前 1 天"]].forEach(([value, label]) => {
  const option = document.createElement("option"); option.value = value; option.textContent = label;
  if (value === 30) option.selected = true; reminderLeadSelect.append(option);
});
reminderLeadLabel.append(reminderLeadSelect);
$("reminder-form").insertBefore(reminderLeadLabel, $("reminder-form").querySelector("button"));
const managedPreview = new URLSearchParams(window.location.search).get("managed") === "1" ? JSON.parse(sessionStorage.getItem("tenantCustomerSession") || "null") : null;
if (managedPreview) {
  const previewName = managedPreview.tenantName || "租户客户服务";
  document.querySelector("#start h1").textContent = managedPreview.tenantName;
  document.querySelector("#start p").textContent = "已从平台管理员后台选择该租户及模拟客户账号。进入后只能访问本租户的数据和知识库。";
  $("brand-name").textContent = previewName;
  document.querySelector(".brand-mark").textContent = previewName.slice(0, 1);
  document.title = `${previewName}·家长服务中心`;
  $("start-button").textContent = "以所选客户账号进入";
}
function uid(prefix) { return `${prefix}-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`; }
function requireSession(node) { if (customerSession) return true; Demo.setStatus(node, "请先进入演示平台。", true); return false; }
function showPanel(name) {
  document.querySelectorAll(".portal-panel").forEach((node) => node.classList.toggle("active", node.id === `panel-${name}`));
  document.querySelectorAll(".portal-tab").forEach((node) => node.classList.toggle("active", node.dataset.panel === name));
  if (name === "services") loadSubscription();
  if (name === "reminders") loadReminders();
}
document.querySelectorAll(".jump[data-panel], .quick-action[data-panel]").forEach((button) => {
  const hint = document.createElement("small"); hint.className = "jump-hint"; hint.textContent = `点击可跳转至${panelNames[button.dataset.panel]}窗口`;
  button.append(hint);
});
document.querySelectorAll("[data-panel]").forEach((button) => button.addEventListener("click", () => showPanel(button.dataset.panel)));

function applyEnabledFeatures(features) {
  const enabled = new Set(features || []);
  const panelFeature = { learning: "learning", finance: "finance", services: "services", reminders: "reminders", assistant: "assistant" };
  Object.entries(panelFeature).forEach(([panel, feature]) => {
    const allowed = enabled.has(feature);
    document.querySelectorAll(`.portal-tab[data-panel="${panel}"], .jump[data-panel="${panel}"], .quick-action[data-panel="${panel}"]`).forEach((node) => { node.hidden = !allowed; });
    const panelNode = $(`panel-${panel}`); if (panelNode) panelNode.hidden = !allowed;
  });
  const handoffBar = document.querySelector(".handoff-bar");
  if (handoffBar) handoffBar.hidden = !enabled.has("handoff");
}

function renderSuggestions(suggestions) {
  const container = $("suggestion-list"); container.replaceChildren();
  (suggestions.length ? suggestions : ["你们有哪些课程？", "请假和补课有什么规则？", "退费多久到账？"]).forEach((text) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "suggestion-chip"; button.textContent = text;
    button.addEventListener("click", () => { $("chat-input").value = text; $("chat-input").focus(); }); container.append(button);
  });
}
async function loadSuggestions() { const result = await Demo.api("/knowledge/suggestions", { token: customerSession.token }); renderSuggestions(result.suggestions || []); }

const actionDock = document.createElement("div"); actionDock.className = "suggestion-list"; actionDock.hidden = true;
$("messages").before(actionDock);
function showContextActions(text) {
  actionDock.replaceChildren();
  actionRules.filter(([pattern]) => pattern.test(text)).forEach(([, panel, label]) => {
    const button = document.createElement("button"); button.type = "button"; button.className = "suggestion-chip"; button.textContent = `立即${label}`;
    button.textContent = `点击可跳转至${label}窗口`;
    button.addEventListener("click", () => showPanel(panel)); actionDock.append(button);
  });
  actionDock.hidden = !actionDock.children.length;
}
$("chat-input").addEventListener("input", (event) => showContextActions(event.target.value));

function actionButton(label, handler, className = "button secondary") {
  const button = document.createElement("button"); button.type = "button"; button.className = className; button.textContent = label;
  button.addEventListener("click", handler); return button;
}
function decorateAssistantReply(node, item, index, messages) {
  if (item.role !== "assistant" || index === 0 || messages[index - 1]?.role !== "user") return;
  const question = messages[index - 1].content || "";
  const panel = document.createElement("div"); panel.className = "inline-action-card";
  const status = document.createElement("div"); status.className = "status";
  if (/关闭.*续费|取消.*续费/.test(question)) {
    if (item.content.includes("未开启自动续费")) return;
    const confirmationId = item.content.match(/[0-9a-f]{8}-[0-9a-f-]{27,}/i)?.[0];
    panel.append(actionButton("确认取消自动续费", async () => { try { if (!confirmationId) throw new Error("确认请求已过期，请重新提问。"); await Demo.api(`/commands/confirm/${confirmationId}`, { method: "POST", token: customerSession.token }); Demo.setStatus(status, "自动续费已取消。可随时重新开启。"); panel.querySelectorAll("button").forEach((button) => { button.disabled = true; }); } catch (error) { Demo.setStatus(status, error.message, true); } }, "button danger"), actionButton("保留自动续费", () => { panel.replaceChildren(); Demo.setStatus(status, "已保留自动续费，本次未做任何更改。"); panel.append(status); }));
  } else if (/提醒/.test(question)) {
    const content = document.createElement("input"); content.value = "数学思维课准备文具和课本"; content.placeholder = "提醒事项";
    const time = document.createElement("input"); time.type = "datetime-local"; const next = new Date(Date.now() + 86400000); next.setHours(18, 0, 0, 0); time.value = new Date(next.getTime() - next.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
    const lead = reminderLeadSelect.cloneNode(true); lead.removeAttribute("id");
    panel.append(content, time, lead, actionButton("确认创建提醒", async () => { try { const result = await Demo.api("/reminders", { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, content: content.value, run_at_local: time.value, timezone: "Asia/Shanghai", repeat: "once", lead_time_minutes: Number(lead.value) }) }); Demo.setStatus(status, `事项时间：${new Date(result.scheduled_for_at).toLocaleString("zh-CN")}，将提前 ${result.lead_time_minutes} 分钟通知。`); } catch (error) { Demo.setStatus(status, error.message, true); } }));
  } else if (/课表/.test(question)) {
    panel.append(actionButton("查看我的课表（点击可跳转至学习中心窗口）", () => { showPanel("learning"); $("load-schedule").click(); }));
  } else if (/学习报告|学习进度|成绩/.test(question)) {
    panel.append(actionButton("打开学习报告（点击可跳转至学习中心窗口）", () => { showPanel("learning"); $("load-report").click(); }));
  } else if (/订单|账单|发票|退款|退费|余额/.test(question)) {
    panel.append(actionButton("查看订单（点击可跳转至财务中心窗口）", () => { showPanel("finance"); document.querySelector('[data-finance="order"]').click(); }), actionButton("查看发票（点击可跳转至财务中心窗口）", () => { showPanel("finance"); document.querySelector('[data-finance="invoice"]').click(); }));
  } else if (/请假|缺席/.test(question)) {
    panel.append(actionButton("填写请假申请（点击可跳转至办事服务窗口）", () => { showPanel("services"); $("leave-date").focus(); }));
  } else if (/人工|客服/.test(question)) {
    const message = document.createElement("textarea"); message.placeholder = "请输入需要人工客服处理的问题"; message.value = "请客服在工作时间联系我，协助处理课程问题。";
    panel.append(message, actionButton("提交留言", async () => { try { if (!message.value.trim()) throw new Error("请先填写留言内容。"); const result = await Demo.api("/handoffs", { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, summary: "客户离线留言", reason: "offline_message", attempted_actions: ["rag_assistant"], message: message.value.trim() }) }); message.disabled = true; Demo.setStatus(status, `留言已提交（${result.id.slice(0, 8)}），客服会在工作时间联系您。`); } catch (error) { Demo.setStatus(status, error.message, true); } }));
  }
  if (panel.children.length) { panel.append(status); node.append(panel); }
}
Demo.setMessageDecorator(decorateAssistantReply);

$("start-button").addEventListener("click", async () => {
  $("start-button").disabled = true; Demo.setStatus($("start-status"), "正在准备专属演示数据…");
  try {
    const managed = new URLSearchParams(window.location.search).get("managed") === "1" ? JSON.parse(sessionStorage.getItem("tenantCustomerSession") || "null") : null;
    const data = managed || await Demo.api("/demo/customer-session", { method: "POST" });
    customerSession = { token: data.token || data.access_token, tenantId: data.tenantId || data.tenant_id, userId: data.userId || data.user_id, conversationId: data.conversationId || data.conversation_id, features: data.features || [], startedAt: new Date(Date.now() - 1000) };
    applyEnabledFeatures(customerSession.features);
    const tenantName = data.tenantName || data.tenant_name; const customerName = data.customerName || data.customer_name || "家长";
    $("tenant-name").textContent = `${tenantName}·智能客服`; $("brand-name").textContent = tenantName; $("user-chip").textContent = customerName; $("user-chip").hidden = false;
    $("welcome-customer").textContent = `${customerName}，欢迎回来`; $("welcome-tenant").textContent = tenantName; document.title = `${tenantName}·家长服务中心`; document.querySelector(".brand-mark").textContent = tenantName.slice(0, 1);
    $("start").hidden = true; $("portal").hidden = false; $("leave-date").value = "2026-09-24";
    const tomorrow = new Date(Date.now() + 86400000); tomorrow.setHours(18, 0, 0, 0); const local = new Date(tomorrow.getTime() - tomorrow.getTimezoneOffset() * 60000); $("reminder-time").value = local.toISOString().slice(0, 16);
    renderSuggestions(managed ? [...recordingPrompts, "隔壁公司的课程和优惠是什么？"] : recordingPrompts);
    await Promise.all([Demo.loadMessages(customerSession, $("messages")), loadActiveHandoff()]);
  } catch (error) { Demo.setStatus($("start-status"), error.message, true); $("start-button").disabled = false; }
});

$("load-schedule").addEventListener("click", async () => {
  if (!requireSession($("schedule-status"))) return; Demo.setStatus($("schedule-status"), "正在读取课表…");
  try {
    const result = await Demo.api("/platform/course-schedule", { token: customerSession.token }); const box = $("schedule-list"); box.replaceChildren();
    (result.data || []).forEach((course) => { const card = document.createElement("article"); card.className = "data-item"; const date = document.createElement("div"); date.className = "date-tile"; date.innerHTML = `<strong>${new Date(course.starts_at).getDate()}</strong><span>9月</span>`; const body = document.createElement("div"); const title = document.createElement("strong"); const meta = document.createElement("p"); const place = document.createElement("small"); title.textContent = course.course_name; meta.textContent = `${new Date(course.starts_at).toLocaleString("zh-CN")} · ${course.teacher}`; place.textContent = `${course.campus} ${course.classroom}`; body.append(title, meta, place); card.append(date, body); box.append(card); });
    Demo.setStatus($("schedule-status"), `已加载 ${result.data?.length || 0} 节课程。`);
  } catch (error) { Demo.setStatus($("schedule-status"), error.message, true); }
});

$("load-report").addEventListener("click", async () => {
  if (!requireSession($("report-status"))) return; Demo.setStatus($("report-status"), "正在读取报告…");
  try {
    const result = await Demo.api("/platform/study-report", { token: customerSession.token }); const d = result.data || {}; const box = $("report-card"); box.className = "report-card"; box.replaceChildren();
    [["完成课时", d.completed_lessons], ["出勤率", `${Math.round((d.attendance_rate || 0) * 100)}%`], ["作业完成率", `${Math.round((d.homework_rate || 0) * 100)}%`], ["能力增长", d.skill_growth]].forEach(([label, value]) => { const item = document.createElement("div"); item.className = "report-metric"; const strong = document.createElement("strong"); const span = document.createElement("span"); strong.textContent = value; span.textContent = label; item.append(strong, span); box.append(item); });
    const comment = document.createElement("p"); comment.className = "teacher-comment"; comment.textContent = `教师评语：${d.teacher_comment}`; box.append(comment); Demo.setStatus($("report-status"), "学习报告已更新。");
  } catch (error) { Demo.setStatus($("report-status"), error.message, true); }
});

document.querySelectorAll("[data-finance]").forEach((button) => button.addEventListener("click", async () => {
  if (!requireSession($("finance-status"))) return; const kind = button.dataset.finance; Demo.setStatus($("finance-status"), `正在查询${financeLabels[kind]}…`);
  try {
    const response = await Demo.api(`/finance/${kind}`, { token: customerSession.token }); if (response.status !== "ok") throw new Error(response.message || "财务系统暂不可用"); const data = response.data || {}; const result = $("finance-result"); result.replaceChildren(); const title = document.createElement("h3"); title.textContent = financeLabels[kind]; const grid = document.createElement("dl"); grid.className = "detail-grid"; result.append(title, grid);
    Object.entries(data).filter(([key]) => !["tenant_id", "user_id"].includes(key)).forEach(([key, value]) => { const dt = document.createElement("dt"); const dd = document.createElement("dd"); dt.textContent = fieldLabels[key] || key; dd.textContent = ["amount", "balance"].includes(key) ? `¥${Number(value).toFixed(2)}` : String(value); grid.append(dt, dd); });
    Demo.setStatus($("finance-status"), "查询成功，操作已记录审计日志。");
  } catch (error) { Demo.setStatus($("finance-status"), error.message, true); }
}));

async function executeCommand(action, resourceId, args, node) {
  const result = await Demo.api(`/commands/${action}`, { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, resource_id: resourceId, arguments: args, idempotency_key: uid(action) }) });
  Demo.setStatus(node, `操作成功，执行号：${result.execution_id.slice(0, 8)}`); return result;
}
$("leave-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!requireSession($("leave-status"))) return; try { await executeCommand("submit-leave", $("leave-course").value, { date: $("leave-date").value, reason: $("leave-reason").value }, $("leave-status")); } catch (error) { Demo.setStatus($("leave-status"), error.message, true); } });
$("course-reminder-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!requireSession($("course-reminder-status"))) return; try { await executeCommand("update-course-reminder", "all-courses", { minutes_before: Number($("course-reminder-minutes").value), channel: $("course-reminder-channel").value }, $("course-reminder-status")); } catch (error) { Demo.setStatus($("course-reminder-status"), error.message, true); } });

async function loadSubscription() {
  if (!customerSession) return;
  try { const result = await Demo.api("/platform/subscription-status", { token: customerSession.token }); const d = result.data || {}; const box = $("subscription-card"); box.replaceChildren(); const badge = document.createElement("span"); badge.className = `state-badge ${d.auto_renew ? "on" : "off"}`; badge.textContent = d.auto_renew ? "已开启" : "已关闭"; const title = document.createElement("strong"); title.textContent = d.plan; const detail = document.createElement("p"); detail.textContent = `下次扣款：${new Date(d.next_charge_at).toLocaleString("zh-CN")}`; box.append(badge, title, detail); } catch (error) { Demo.setStatus($("renew-status"), error.message, true); }
}
$("open-renew").addEventListener("click", async () => { if (!requireSession($("renew-status"))) return; try { await executeCommand("open-auto-renew", "membership-2026", { plan: "annual" }, $("renew-status")); $("renew-confirm").hidden = true; await loadSubscription(); } catch (error) { Demo.setStatus($("renew-status"), error.message, true); } });
$("close-renew").addEventListener("click", async () => { if (!requireSession($("renew-status"))) return; try { const result = await Demo.api("/commands/close-auto-renew", { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, resource_id: "membership-2026" }) }); pendingConfirmationId = result.confirmation_id; $("renew-confirm").hidden = false; Demo.setStatus($("renew-status"), "关闭请求已创建，请再次确认。"); } catch (error) { Demo.setStatus($("renew-status"), error.message, true); } });
$("confirm-close-renew").addEventListener("click", async () => { if (!pendingConfirmationId) return; try { await Demo.api(`/commands/confirm/${pendingConfirmationId}`, { method: "POST", token: customerSession.token }); pendingConfirmationId = null; $("renew-confirm").hidden = true; Demo.setStatus($("renew-status"), "自动续费已关闭。"); await loadSubscription(); } catch (error) { Demo.setStatus($("renew-status"), error.message, true); } });

let currentHandoff = null;
const handoffLabels = { pending: "待处理", in_progress: "处理中", awaiting_confirmation: "待确认结束", ended: "已结束" };
async function loadActiveHandoff() {
  if (!customerSession || !customerSession.features.includes("handoff")) return;
  try {
    const result = await Demo.api(`/handoffs/active?conversation_id=${customerSession.conversationId}`, { token: customerSession.token });
    currentHandoff = result.handoff;
    const state = $("handoff-state");
    if (!currentHandoff) { state.textContent = "未发起或已结束"; state.className = "state-badge off"; $("handoff-close-actions").hidden = true; $("handoff-close-prompt").hidden = true; customerSession.humanMode = false; return; }
    state.textContent = handoffLabels[currentHandoff.status] || currentHandoff.status; state.className = `state-badge handoff-${currentHandoff.status}`;
    $("handoff-close-actions").hidden = currentHandoff.status !== "awaiting_confirmation";
    $("handoff-close-prompt").hidden = currentHandoff.status !== "awaiting_confirmation";
    customerSession.humanMode = ["pending", "in_progress", "awaiting_confirmation"].includes(currentHandoff.status);
    if (currentHandoff.status === "awaiting_confirmation") Demo.setStatus($("handoff-status"), "客服申请结束人工服务，请在 10 分钟内确认；未回应将自动结束。");
    if (currentHandoff.status === "in_progress") Demo.setStatus($("handoff-status"), "人工客服处理中，AI 已暂停回复。");
    await Demo.loadMessages(customerSession, $("messages"));
  } catch (error) { Demo.setStatus($("handoff-status"), error.message, true); }
}
$("handoff-button").addEventListener("click", async () => { if (!requireSession($("handoff-status"))) return; try { currentHandoff = await Demo.api("/handoffs", { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, summary: "家长请求人工客服协助", reason: "explicit_request", attempted_actions: ["self_service_portal", "rag_assistant"] }) }); customerSession.humanMode = true; Demo.setStatus($("handoff-status"), "人工请求已提交，当前状态：待处理。"); await loadActiveHandoff(); } catch (error) { Demo.setStatus($("handoff-status"), error.message, true); } });
async function respondToHandoffClose(confirm) {
  if (!currentHandoff) return;
  const buttons = [$("confirm-handoff-close"), $("continue-handoff"), $("prompt-confirm-handoff-close"), $("prompt-continue-handoff")];
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const result = await Demo.api(`/handoffs/${currentHandoff.id}/close-response`, { method: "POST", token: customerSession.token, body: JSON.stringify({ confirm }) });
    currentHandoff = confirm ? null : result;
    customerSession.humanMode = !confirm;
    const message = confirm ? "人工客服已结束，后续消息将由 AI 客服处理。" : "已通知客服问题尚未解决，将继续人工服务。";
    Demo.setStatus($("handoff-status"), message); Demo.setStatus($("handoff-close-prompt-status"), message);
    if (confirm) $("handoff-close-prompt").hidden = true;
    await loadActiveHandoff();
  } catch (error) {
    Demo.setStatus($("handoff-close-prompt-status"), error.message, true);
  } finally { buttons.forEach((button) => { button.disabled = false; }); }
}
$("confirm-handoff-close").addEventListener("click", () => respondToHandoffClose(true));
$("continue-handoff").addEventListener("click", () => respondToHandoffClose(false));
$("prompt-confirm-handoff-close").addEventListener("click", () => respondToHandoffClose(true));
$("prompt-continue-handoff").addEventListener("click", () => respondToHandoffClose(false));

async function loadReminders() {
  if (!customerSession) return;
  try { const result = await Demo.api("/reminders", { token: customerSession.token }); const box = $("reminder-list"); box.replaceChildren(); if (!(result.reminders || []).length) { const empty = document.createElement("div"); empty.className = "empty compact"; empty.textContent = "暂无提醒，可在左侧新建。"; box.append(empty); return; }
    result.reminders.forEach((item) => { const card = document.createElement("article"); card.className = `reminder-item ${item.status}`; const body = document.createElement("div"); const title = document.createElement("strong"); title.textContent = item.content; const meta = document.createElement("p"); meta.textContent = `事项：${new Date(item.scheduled_for_at).toLocaleString("zh-CN")} · 提前 ${item.lead_time_minutes} 分钟 · ${item.repeat} · ${item.status}`; body.append(title, meta); const actions = document.createElement("div"); actions.className = "row"; const edit = document.createElement("button"); edit.className = "text-button"; edit.textContent = "修改"; edit.disabled = item.status !== "active"; edit.addEventListener("click", async () => { const content = window.prompt("修改提醒内容", item.content); if (!content || content === item.content) return; await Demo.api(`/reminders/${item.id}`, { method: "PATCH", token: customerSession.token, body: JSON.stringify({ content, version: item.version }) }); await loadReminders(); }); const cancel = document.createElement("button"); cancel.className = "text-button danger-text"; cancel.textContent = "取消"; cancel.disabled = item.status !== "active"; cancel.addEventListener("click", async () => { await Demo.api(`/reminders/${item.id}`, { method: "DELETE", token: customerSession.token }); await loadReminders(); }); actions.append(edit, cancel); card.append(body, actions); box.append(card); });
  } catch (error) { Demo.setStatus($("reminder-list-status"), error.message, true); }
}
$("reminder-form").addEventListener("submit", async (event) => { event.preventDefault(); if (!requireSession($("reminder-form-status"))) return; try { await Demo.api("/reminders", { method: "POST", token: customerSession.token, body: JSON.stringify({ conversation_id: customerSession.conversationId, content: $("reminder-content").value, run_at_local: $("reminder-time").value, timezone: "Asia/Shanghai", repeat: $("reminder-repeat").value, lead_time_minutes: Number($("reminder-lead-time").value) }) }); Demo.setStatus($("reminder-form-status"), "提醒创建成功。"); await loadReminders(); } catch (error) { Demo.setStatus($("reminder-form-status"), error.message, true); } });
$("refresh-reminders").addEventListener("click", loadReminders);
Demo.bindComposer($("chat-form"), $("chat-input"), $("chat-status"), () => customerSession, $("messages"));
setInterval(loadActiveHandoff, 2000);
