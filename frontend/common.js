(() => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let messageDecorator = null;

  async function api(path, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (options.token) headers.Authorization = `Bearer ${options.token}`;
    if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    const response = await fetch(path, { ...options, headers });
    let body = null;
    try { body = await response.json(); } catch (_) { body = {}; }
    if (!response.ok) throw new Error(body.message || body.detail || `HTTP ${response.status}`);
    return body;
  }

  function renderMessages(container, messages) {
    container.replaceChildren();
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "还没有消息，输入一个问题开始测试。";
      container.append(empty);
      return;
    }
    messages.forEach((item, index) => {
      const node = document.createElement("div");
      node.className = `bubble ${item.role === "user" ? "user" : "assistant"}`;
      const text = document.createElement("div"); text.textContent = item.content; node.append(text);
      if (messageDecorator) messageDecorator(node, item, index, messages);
      container.append(node);
    });
    container.scrollTop = container.scrollHeight;
  }

  async function loadMessages(session, container) {
    const body = await api(`/conversations/${session.conversationId}/messages`, { token: session.token });
    const messages = (body.messages || []).filter((item) => !session.startedAt || !item.created_at || new Date(item.created_at) >= session.startedAt);
    renderMessages(container, messages);
    return messages;
  }

  async function sendAndPoll(session, content, container) {
    const messageId = crypto.randomUUID();
    await api("/webhooks/im/messages", {
      method: "POST",
      token: session.token,
      body: JSON.stringify({
        message_id: messageId,
        tenant_id: session.tenantId,
        user_id: session.userId,
        conversation_id: session.conversationId,
        content,
      }),
    });
    for (let index = 0; index < 30; index += 1) {
      const messages = await loadMessages(session, container);
      const userIndex = messages.findIndex((item) => item.message_id === messageId);
      if (session.humanMode && userIndex >= 0) return;
      if (userIndex >= 0 && messages.slice(userIndex + 1).some((item) => item.role === "assistant")) return;
      await sleep(700);
    }
    throw new Error("回复等待超时，请稍后刷新消息。");
  }

  function bindComposer(form, input, status, getSession, container) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const session = getSession();
      const content = input.value.trim();
      if (!session) return setStatus(status, "请先建立会话。", true);
      if (!content) return;
      const button = form.querySelector("button");
      button.disabled = true;
      try {
        if (session.liveHandoff && !session.humanMode && /人工|客服|老师回复/.test(content)) {
          await api("/handoffs", { method: "POST", token: session.token, body: JSON.stringify({ conversation_id: session.conversationId, summary: content, reason: "explicit_request", attempted_actions: ["rag_assistant"] }) });
          session.humanMode = true;
          if (session.onHandoff) await session.onHandoff();
        }
        input.value = "";
        setStatus(status, session.humanMode ? "已发送给人工客服。" : "已发送，正在等待 AI 回复…");
        await sendAndPoll(session, content, container);
        setStatus(status, session.humanMode ? "已送达人工客服。" : "");
      } catch (error) {
        setStatus(status, error.message, true);
      } finally {
        button.disabled = false;
        input.focus();
      }
    });
  }

  function setStatus(node, text, isError = false) {
    node.textContent = text;
    node.className = `status ${isError ? "error" : text ? "ok" : ""}`;
  }

  function setMessageDecorator(decorator) { messageDecorator = decorator; }

  window.Demo = { api, bindComposer, loadMessages, renderMessages, setStatus, setMessageDecorator };
})();
