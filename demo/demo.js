(() => {
  "use strict";

  const themes = new Set(["jade", "sky", "sunset"]);
  const root = document.documentElement;
  const storedTheme = localStorage.getItem("token-loom-theme");
  const theme = themes.has(storedTheme) ? storedTheme : "jade";
  const dark = localStorage.getItem("token-loom-mode") === "dark";
  root.dataset.theme = theme;
  if (dark) root.dataset.mode = "dark";

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const syncModeLabel = () => {
    const isDark = root.dataset.mode === "dark";
    const label = isDark ? "切换到浅色模式" : "切换到深灰模式";
    $("#dark-toggle").setAttribute("aria-label", label);
    $("#dark-toggle").setAttribute("title", label);
  };
  syncModeLabel();
  $$('[data-theme-choice]').forEach(button => {
    button.classList.toggle("active", button.dataset.themeChoice === theme);
    button.addEventListener("click", () => {
      root.dataset.theme = button.dataset.themeChoice;
      localStorage.setItem("token-loom-theme", button.dataset.themeChoice);
      $$('[data-theme-choice]').forEach(item => item.classList.toggle("active", item === button));
    });
  });
  $("#palette-button").addEventListener("click", event => {
    event.stopPropagation();
    $("#theme-popover").hidden = !$("#theme-popover").hidden;
  });
  document.addEventListener("click", event => {
    if (!event.target.closest(".theme-wrap")) $("#theme-popover").hidden = true;
  });
  $("#dark-toggle").addEventListener("click", () => {
    const next = root.dataset.mode !== "dark";
    if (next) root.dataset.mode = "dark";
    else delete root.dataset.mode;
    localStorage.setItem("token-loom-mode", next ? "dark" : "light");
    syncModeLabel();
  });
  $("#mobile-menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

  const toast = message => {
    const item = document.createElement("div");
    item.className = "toast success";
    item.innerHTML = '<span class="status-dot"></span><div><b>交互预览</b><p></p></div>';
    item.querySelector("p").textContent = `${message}：这是不连接后端的合成演示。`;
    $("#toast-stack").append(item);
    setTimeout(() => item.remove(), 3200);
  };
  $$('[data-demo-action]').forEach(button => button.addEventListener("click", () => toast(button.dataset.demoAction)));
})();
