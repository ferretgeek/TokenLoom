(() => {
  "use strict";

  const themes = new Set(["jade", "sky", "sunset"]);
  const storedTheme = localStorage.getItem("token-loom-theme");
  const selectedTheme = themes.has(storedTheme) ? storedTheme : "jade";
  const selectedMode = localStorage.getItem("token-loom-mode") === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = selectedTheme;
  if (selectedMode === "dark") document.documentElement.dataset.mode = "dark";
  document.querySelectorAll("[data-theme-choice]").forEach(button => {
    button.classList.toggle("active", button.dataset.themeChoice === selectedTheme);
    button.addEventListener("click", () => {
      document.documentElement.dataset.theme = button.dataset.themeChoice;
      localStorage.setItem("token-loom-theme", button.dataset.themeChoice);
      document.querySelectorAll("[data-theme-choice]").forEach(item => item.classList.toggle("active", item === button));
    });
  });
  const darkToggle = document.querySelector("#login-dark-toggle");
  const syncDarkLabel = dark => {
    const label = dark ? "切换到浅色模式" : "切换到深灰模式";
    darkToggle?.setAttribute("aria-label", label);
    darkToggle?.setAttribute("title", label);
  };
  syncDarkLabel(selectedMode === "dark");
  darkToggle?.addEventListener("click", () => {
    const dark = document.documentElement.dataset.mode !== "dark";
    if (dark) document.documentElement.dataset.mode = "dark";
    else delete document.documentElement.dataset.mode;
    localStorage.setItem("token-loom-mode", dark ? "dark" : "light");
    syncDarkLabel(dark);
  });

  const form = document.querySelector("#login-form");
  const input = document.querySelector("#admin-key");
  const button = document.querySelector("#login-submit");
  const label = button?.querySelector("[data-login-label]");
  const spinner = button?.querySelector(".login-spinner");
  const status = document.querySelector("#login-status");
  if (!form || !input || !button || !label || !spinner || !status) return;

  let submitting = false;

  function reset() {
    submitting = false;
    form.classList.remove("is-submitting");
    button.disabled = false;
    button.removeAttribute("aria-busy");
    label.textContent = "进入控制台";
    spinner.hidden = true;
    status.hidden = true;
    status.textContent = "";
  }

  form.addEventListener("submit", event => {
    input.value = input.value.trim();
    if (!input.value) {
      event.preventDefault();
      status.hidden = false;
      status.textContent = "请先输入管理员密钥。";
      input.focus();
      return;
    }
    if (submitting) {
      event.preventDefault();
      return;
    }

    submitting = true;
    form.classList.add("is-submitting");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    label.textContent = "正在验证";
    spinner.hidden = false;
    status.hidden = false;
    status.textContent = "正在验证密钥并建立会话，请稍候…";
  });

  window.addEventListener("pageshow", reset);
})();
