(() => {
  "use strict";

  const storageKey = "minimal-agent.deepseek-api-key";
  const maxKeyLength = 512;

  function readKey() {
    try {
      return window.localStorage.getItem(storageKey) || "";
    } catch (_) {
      return "";
    }
  }

  function setStatus(message, hasKey) {
    const status = document.querySelector("#browser-key-status");
    const badge = document.querySelector("#browser-key-badge");
    if (status) {
      status.textContent = message;
    }
    if (badge) {
      badge.textContent = hasKey ? "已保存在此浏览器" : "未保存";
      badge.classList.toggle("is-ready", hasKey);
    }
  }

  function updateKeyState(message) {
    const hasKey = Boolean(readKey());
    setStatus(message || (hasKey ? "聊天请求将使用浏览器缓存的密钥。" : "可在此保存密钥，或继续使用服务端 .env 配置。"), hasKey);
  }

  function initializeKeyControls() {
    const input = document.querySelector("#browser-api-key-input");
    const save = document.querySelector("#browser-key-save");
    const clear = document.querySelector("#browser-key-clear");
    if (!input || !save || !clear) {
      return;
    }

    updateKeyState();
    save.addEventListener("click", () => {
      const key = input.value.trim();
      if (!key) {
        updateKeyState("请输入 DeepSeek API Key 后再保存。");
        input.focus();
        return;
      }
      if (key.length > maxKeyLength) {
        updateKeyState("密钥长度无效，请检查后重试。");
        return;
      }
      try {
        window.localStorage.setItem(storageKey, key);
        input.value = "";
        updateKeyState("已保存在当前浏览器；密钥不会显示在页面中。");
      } catch (_) {
        updateKeyState("浏览器拒绝保存本地缓存，请检查隐私设置。");
      }
    });
    clear.addEventListener("click", () => {
      try {
        window.localStorage.removeItem(storageKey);
        input.value = "";
        updateKeyState("已清除当前浏览器缓存的密钥。");
      } catch (_) {
        updateKeyState("浏览器拒绝修改本地缓存，请检查隐私设置。");
      }
    });
  }

  function isChatForm(form) {
    return form instanceof HTMLFormElement && /\/sessions\/[^/]+\/messages$/.test(form.action);
  }

  async function submitChatWithoutHtmx(event) {
    const form = event.target;
    if (!isChatForm(form) || window.htmx) {
      return;
    }
    event.preventDefault();
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
    }

    try {
      const key = readKey();
      const headers = {"HX-Request": "true"};
      if (key) {
        headers["X-DeepSeek-API-Key"] = key;
      }
      const response = await window.fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers,
        credentials: "same-origin",
      });
      const content = await response.text();
      const fragment = document.createRange().createContextualFragment(content);
      const nextChat = fragment.querySelector("#chat-panel");
      const nextTodos = fragment.querySelector("#todos-panel");
      const currentChat = document.querySelector("#chat-panel");
      const currentTodos = document.querySelector("#todos-panel");
      if (nextChat && currentChat) {
        currentChat.replaceWith(nextChat);
      }
      if (nextTodos && currentTodos) {
        currentTodos.replaceWith(nextTodos);
      }
      if (!nextChat) {
        form.insertAdjacentHTML("afterend", content);
      }
      if (response.ok) {
        form.reset();
      }
    } catch (_) {
      form.insertAdjacentHTML(
        "afterend",
        '<p class="error-message" role="alert">请求发送失败，请检查网络连接后重试。</p>',
      );
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
      }
    }
  }

  document.addEventListener("DOMContentLoaded", initializeKeyControls);
  document.addEventListener("submit", submitChatWithoutHtmx);

  document.body.addEventListener("htmx:configRequest", (event) => {
    const path = event.detail.path || event.detail.elt?.getAttribute("hx-post") || "";
    const key = readKey();
    if (key && /\/sessions\/[^/]+\/messages$/.test(path)) {
      event.detail.headers ||= {};
      event.detail.headers["X-DeepSeek-API-Key"] = key;
    }
  });
})();
