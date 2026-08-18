(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || script.dataset.topmedLoaded === "true") return;
  script.dataset.topmedLoaded = "true";

  function httpUrl(value, fallback) {
    try {
      var url = new URL(value || fallback, script.src);
      return url.protocol === "http:" || url.protocol === "https:" ? url : null;
    } catch {
      return null;
    }
  }

  var assistantKey = script.dataset.assistantKey;
  var scriptUrl = httpUrl(script.src);
  var chatUrl = httpUrl(script.dataset.chatUrl, scriptUrl && scriptUrl.origin);
  var apiUrl = httpUrl(script.dataset.apiUrl);
  if (!assistantKey || !chatUrl) {
    console.error("TopMed widget: data-assistant-key and a valid chat URL are required.");
    return;
  }

  var position = script.dataset.position === "bottom-left" ? "left" : "right";
  var root = document.createElement("div");
  root.id = "topmed-widget-root";
  var shadow = root.attachShadow({ mode: "closed" });
  var style = document.createElement("style");
  style.textContent = [
    ":host{all:initial}",
    ".tm-launcher{position:fixed;z-index:2147483647;bottom:20px;" + position + ":20px;width:58px;height:58px;border:0;border-radius:20px;background:#0f766e;color:#fff;box-shadow:0 14px 34px rgba(15,23,42,.3);display:grid;place-items:center;cursor:pointer;transition:transform .18s ease,background .18s ease}",
    ".tm-launcher:hover{background:#115e59;transform:translateY(-2px)}",
    ".tm-launcher:focus-visible{outline:3px solid #5eead4;outline-offset:3px}",
    ".tm-launcher svg{width:25px;height:25px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}",
    ".tm-badge{position:absolute;top:-5px;right:-5px;min-width:20px;height:20px;padding:0 5px;border:2px solid #fff;border-radius:99px;background:#e11d48;color:#fff;font:700 11px/16px system-ui;text-align:center}",
    ".tm-frame{position:fixed;z-index:2147483646;bottom:90px;" + position + ":20px;width:min(400px,calc(100vw - 32px));height:min(680px,calc(100dvh - 112px));border:1px solid #dbe3ea;border-radius:24px;background:#fff;box-shadow:0 24px 70px rgba(15,23,42,.25);overflow:hidden;opacity:0;visibility:hidden;transform:translateY(12px) scale(.98);transform-origin:bottom " + position + ";transition:opacity .18s ease,transform .18s ease,visibility .18s ease}",
    ".tm-frame[data-open=true]{opacity:1;visibility:visible;transform:none}",
    ".tm-frame iframe{width:100%;height:100%;border:0;background:#fff}",
    "@media(max-width:600px){.tm-frame{inset:0;width:100vw;height:100dvh;border:0;border-radius:0}.tm-launcher{bottom:16px;" + position + ":16px}.tm-frame[data-open=true]+.tm-launcher{visibility:hidden}}",
    "@media(prefers-reduced-motion:reduce){.tm-launcher,.tm-frame{transition:none}}",
  ].join("");

  var frameShell = document.createElement("div");
  frameShell.className = "tm-frame";
  frameShell.dataset.open = "false";
  var iframe = document.createElement("iframe");
  iframe.title = "Atendimento TopMed Guide";
  iframe.loading = "lazy";
  iframe.referrerPolicy = "strict-origin-when-cross-origin";
  var widgetUrl = new URL("/widget", chatUrl.origin);
  widgetUrl.searchParams.set("assistantKey", assistantKey);
  widgetUrl.searchParams.set("locale", script.dataset.locale || "pt-BR");
  if (apiUrl) widgetUrl.searchParams.set("apiBaseUrl", apiUrl.origin);
  if (/^https?:$/.test(window.location.protocol)) {
    widgetUrl.searchParams.set("parentOrigin", window.location.origin);
  }
  iframe.src = widgetUrl.toString();
  frameShell.appendChild(iframe);

  var launcher = document.createElement("button");
  launcher.className = "tm-launcher";
  launcher.type = "button";
  launcher.setAttribute("aria-expanded", "false");
  launcher.setAttribute("aria-label", "Abrir atendimento TopMed");
  launcher.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/><path d="M8 9h8M8 13h5"/></svg>';

  var unread = 0;
  function renderUnread() {
    var badge = launcher.querySelector(".tm-badge");
    if (!unread) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "tm-badge";
      badge.setAttribute("aria-label", "mensagens não lidas");
      launcher.appendChild(badge);
    }
    badge.textContent = unread > 9 ? "9+" : String(unread);
  }

  function send(type, payload) {
    if (!iframe.contentWindow) return;
    iframe.contentWindow.postMessage(Object.assign({ type: type }, payload || {}), chatUrl.origin);
  }

  function setOpen(open) {
    frameShell.dataset.open = String(open);
    launcher.setAttribute("aria-expanded", String(open));
    launcher.setAttribute("aria-label", open ? "Fechar atendimento TopMed" : "Abrir atendimento TopMed");
    if (open) {
      unread = 0;
      renderUnread();
    }
    send("topmed.widget.visibility", { open: open });
  }

  launcher.addEventListener("click", function () {
    setOpen(frameShell.dataset.open !== "true");
  });
  window.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && frameShell.dataset.open === "true") {
      setOpen(false);
      launcher.focus();
    }
  });
  window.addEventListener("message", function (event) {
    if (event.origin !== chatUrl.origin || event.source !== iframe.contentWindow || !event.data) {
      return;
    }
    if (event.data.type === "topmed.widget.ready") {
      send("topmed.widget.visibility", { open: frameShell.dataset.open === "true" });
      send("topmed.widget.theme", { theme: script.dataset.theme || "light" });
    }
    if (event.data.type === "topmed.widget.close") setOpen(false);
    if (event.data.type === "topmed.widget.unread" && frameShell.dataset.open !== "true") {
      unread += Math.max(0, Number(event.data.count) || 0);
      renderUnread();
    }
  });

  shadow.append(style, frameShell, launcher);
  document.body.appendChild(root);
})();
