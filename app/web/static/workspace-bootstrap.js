"use strict";

(() => {
  let csrf = "";
  const identity = fetch("/api/auth/me", { credentials: "same-origin", signal: AbortSignal.timeout(15000) })
    .then(async response => {
      if (!response.ok) { location.assign("/account"); throw new Error("Yeniden giriş yapın."); }
      const data = await response.json();
      csrf = data.csrf_token;
      const avatar = document.querySelector(".user-avatar");
      if (avatar) {
        avatar.textContent = data.user.display_name.slice(0, 1).toLocaleUpperCase("tr-TR");
        avatar.title = data.user.display_name;
      }
      return data;
    });
  window.multirpaTransport = Object.freeze({
    async request(path, body, method) {
      await identity;
      const verb = method || (body === undefined ? "GET" : "POST");
      const headers = { "X-RPA-Request": "1" };
      if (!["GET", "HEAD"].includes(verb)) headers["X-CSRF-Token"] = csrf;
      if (body !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(path, {
        method: verb, headers, credentials: "same-origin",
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });
      let data;
      try { data = await response.json(); } catch { throw new Error("Sunucu yanıtı alınamadı."); }
      if (response.status === 401) location.assign("/account");
      if (!response.ok) throw new Error(data.error?.message || "İşlem tamamlanamadı.");
      return data;
    },
  });
  const accountLink = document.createElement("a");
  accountLink.href = "/account";
  accountLink.className = "button small";
  accountLink.textContent = "Hesabım ve lisans";
  document.querySelector(".topbar-right")?.append(accountLink);
})();
