(() => {
  "use strict";

  const storageKeyPrefix = "minimal-agent.deepseek-api-key:";
  const maxKeyLength = 512;

  function storageKeyForCurrentUser() {
    const userId = document.body.dataset.currentUserId || "";
    return userId ? `${storageKeyPrefix}${userId}` : null;
  }

  function readKey() {
    const storageKey = storageKeyForCurrentUser();
    if (!storageKey) {
      return "";
    }
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

  function setChatKeyStatus(hasKey) {
    const status = document.querySelector("#browser-key-chat-status");
    if (!status) {
      return;
    }
    status.textContent = hasKey
      ? "已检测到浏览器密钥，本次消息将使用它。"
      : "未检测到浏览器密钥，将使用服务端 .env 配置。";
    status.classList.toggle("is-ready", hasKey);
  }

  function updateKeyState(message) {
    const hasKey = Boolean(readKey());
    setStatus(message || (hasKey ? "聊天请求将使用浏览器缓存的密钥。" : "可在此保存密钥，或继续使用服务端 .env 配置。"), hasKey);
    setChatKeyStatus(hasKey);
  }

  function initializeKeyControls() {
    const input = document.querySelector("#browser-api-key-input");
    const save = document.querySelector("#browser-key-save");
    const clear = document.querySelector("#browser-key-clear");
    updateKeyState();
    if (!input || !save || !clear) {
      return;
    }

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
        const storageKey = storageKeyForCurrentUser();
        if (!storageKey) {
          updateKeyState("当前账号状态无效，请重新登录后再保存密钥。");
          return;
        }
        window.localStorage.setItem(storageKey, key);
        input.value = "";
        updateKeyState("已保存在当前账号的浏览器缓存中；密钥不会显示在页面中。");
      } catch (_) {
        updateKeyState("浏览器拒绝保存本地缓存，请检查隐私设置。");
      }
    });
    clear.addEventListener("click", () => {
      try {
        const storageKey = storageKeyForCurrentUser();
        if (!storageKey) {
          updateKeyState("当前账号状态无效，请重新登录后再清除密钥。");
          return;
        }
        window.localStorage.removeItem(storageKey);
        input.value = "";
        updateKeyState("已清除当前账号的浏览器缓存密钥。");
      } catch (_) {
        updateKeyState("浏览器拒绝修改本地缓存，请检查隐私设置。");
      }
    });
  }

  function initializeEmptySessionCleanup() {
    const leaveElement = document.querySelector("[data-empty-session-leave-url]");
    const leaveUrl = leaveElement?.dataset.emptySessionLeaveUrl;
    if (!leaveUrl || !navigator.sendBeacon) {
      return;
    }
    document.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) {
        return;
      }
      const link = event.target.closest("a[href]");
      if (
        !(link instanceof HTMLAnchorElement)
        || event.defaultPrevented
        || event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
        || link.target
        || link.download
      ) {
        return;
      }
      const destination = new URL(link.href, window.location.href);
      if (
        destination.origin !== window.location.origin
        || destination.href === window.location.href
      ) {
        return;
      }
      event.preventDefault();
      window.fetch(leaveUrl, { method: "POST", credentials: "same-origin" })
        .catch(() => undefined)
        .finally(() => window.location.assign(destination.href));
    });
    window.addEventListener(
      "pagehide",
      () => {
        navigator.sendBeacon(leaveUrl);
      },
      { once: true },
    );
  }

  function initializeMarkdownCopyButtons() {
    document.addEventListener("click", async (event) => {
      if (!(event.target instanceof Element)) {
        return;
      }
      const button = event.target.closest("[data-markdown]");
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      let markdown;
      try {
        markdown = JSON.parse(button.dataset.markdown || "");
      } catch (_) {
        return;
      }
      if (typeof markdown !== "string") {
        return;
      }
      const originalText = button.textContent;
      try {
        await copyText(markdown);
        button.textContent = "已复制 Markdown";
      } catch (_) {
        button.textContent = "复制失败，请手动复制";
      }
      window.setTimeout(() => {
        button.textContent = originalText;
      }, 1800);
    });
  }

  async function copyText(value) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      return;
    }
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
      throw new Error("浏览器拒绝写入剪贴板");
    }
  }

  function isChatRequestForm(form) {
    return form instanceof HTMLFormElement
      && /\/sessions\/[^/]+\/messages(?:\/[^/]+\/retry)?$/.test(form.action);
  }

  async function submitChatWithBrowserKey(event) {
    const form = event.target;
    const key = readKey();
    if (!isChatRequestForm(form) || !key) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const submitButton = form.querySelector('button[type="submit"]');
    if (submitButton) {
      submitButton.disabled = true;
    }

    try {
      const headers = {"HX-Request": "true"};
      headers["X-DeepSeek-API-Key"] = key;
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

  document.addEventListener("DOMContentLoaded", () => {
    initializeKeyControls();
    initializeEmptySessionCleanup();
    initializeMarkdownCopyButtons();
  });
  document.addEventListener("submit", submitChatWithBrowserKey, true);

  document.body.addEventListener("htmx:configRequest", (event) => {
    const path = event.detail.path || event.detail.elt?.getAttribute("hx-post") || "";
    const key = readKey();
    if (key && /\/sessions\/[^/]+\/messages(?:\/[^/]+\/retry)?$/.test(path)) {
      event.detail.headers ||= {};
      event.detail.headers["X-DeepSeek-API-Key"] = key;
    }
  });
})();
