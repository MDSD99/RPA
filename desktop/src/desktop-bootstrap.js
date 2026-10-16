"use strict";
(() => {
  const bridge = window.multirpaDesktop;
  const messages = {
    DESKTOP_LOGIN_REQUIRED: "Çalıştırmak için hesabınıza giriş yapın.",
    DESKTOP_SERVER_UNAVAILABLE:
      "Sunucuya ulaşılamıyor. Taslakları düzenleyebilirsiniz; çalıştırmak için bağlantı gerekli.",
    DESKTOP_SECURE_STORAGE_REQUIRED:
      "Cihaz anahtarı için işletim sisteminizin güvenli anahtar deposunu açın.",
    DESKTOP_LICENSE_REQUIRED: "Önce bu cihazı etkinleştirin.",
    DESKTOP_LICENSE_EXPIRED:
      "Çalışma izni sona erdi. Bağlantınızı ve deneme sürenizi kontrol edin.",
    DESKTOP_LICENSE_INVALID: "Cihaz çalışma izni doğrulanamadı.",
    DESKTOP_FLOW_VERSION_CONFLICT:
      "Akışın sürümü değişti. Yeniden açıp kaydedin.",
    DESKTOP_RUNNER_BUSY:
      "Bu cihazda bir akış çalışıyor. Tamamlanmasını bekleyin.",
    DESKTOP_RETRY_UNSUPPORTED: "Masaüstü çalıştırmaları tek denemeyle başlar.",
    TRIAL_EXPIRED: "Demo tamamlandı.",
    TRIAL_LIMIT_REACHED: "Üç akış hakkınız kullanıldı.",
    DEVICE_ALREADY_BOUND:
      "Hesabınız başka bir cihazda etkin. Yönetici cihaz değişimini onaylamalı.",
    ACCOUNT_NOT_APPROVED: "Hesabınız yönetici onayı bekliyor.",
    EMAIL_NOT_VERIFIED: "Önce e-posta adresinizi doğrulayın.",
    INVALID_CREDENTIALS: "E-posta veya parola hatalı.",
  };
  messages.LICENSE_EXPIRED = "Lisans süresi sona erdi.";
  messages.LICENSE_REVOKED = "Lisansınız yönetici tarafından durduruldu.";
  messages.DEVICE_RESET_PENDING =
    "Cihaz değişimi uygulanıyor. Önceki cihazın çalışma izni sona erdiğinde tekrar deneyin.";
  const readable = (code) =>
    messages[code] ||
    "İşlem tamamlanamadı. Hesabınızı ve bağlantınızı kontrol edin.";
  async function unwrap(promise) {
    const result = await promise;
    if (!result.ok) throw Error(readable(result.error?.code));
    return result.data;
  }
  window.multirpaTransport = Object.freeze({
    request: (path, body, method) =>
      unwrap(
        bridge.request(
          path,
          body,
          method || (body === undefined ? "GET" : "POST"),
        ),
      ),
  });
  if (!location.hash) location.hash = "workflows";
  let current;
  async function status() {
    current = await unwrap(bridge.account());
    render();
    return current;
  }
  function render() {
    const button = document.querySelector("#desktop-account-button");
    if (button)
      button.textContent = current?.online
        ? current.licensed
          ? "Cihaz etkin"
          : "Hesabım"
        : "Giriş yap";
    const note = document.querySelector("#desktop-license-note");
    const trial = current?.trial?.trial || current?.trial;
    if (note)
      note.textContent =
        trial?.state === "expired"
          ? "Demo tamamlandı"
          : current?.licensed
            ? "Bu cihazda çalıştırma açık"
            : current?.user
              ? "Taslak düzenleyebilirsiniz · Çalıştırmak için cihazı etkinleştirin"
              : "Hesabınıza giriş yapın";
  }
  function show() {
    const dialog = document.querySelector("#desktop-account-dialog");
    const content = dialog.querySelector(".desktop-dialog-body");
    content.replaceChildren();
    const heading = document.createElement("h2");
    heading.textContent = "MultiRPA hesabınız";
    content.append(heading);
    const paragraph = document.createElement("p");
    paragraph.textContent = current?.user
      ? `${current.user.display_name} · ${current.user.email}`
      : "Web hesabınızla giriş yapın. Kayıt ve e-posta doğrulama için hesap sayfasını açabilirsiniz.";
    content.append(paragraph);
    const error = document.createElement("p");
    error.className = "desktop-error";
    error.setAttribute("role", "alert");
    const action = (label, operation) => {
      const b = document.createElement("button");
      b.className = "button";
      b.type = "button";
      b.textContent = label;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try {
          await unwrap(operation());
          await status();
          show();
        } catch (e) {
          error.textContent = e.message;
        } finally {
          b.disabled = false;
        }
      });
      content.append(b);
    };
    if (!current?.online) {
      const form = document.createElement("form");
      form.className = "desktop-login";
      const email = document.createElement("input");
      email.type = "email";
      email.required = true;
      email.autocomplete = "username";
      email.placeholder = "E-posta";
      email.setAttribute("aria-label", "E-posta");
      const password = document.createElement("input");
      password.type = "password";
      password.required = true;
      password.maxLength = 128;
      password.autocomplete = "current-password";
      password.placeholder = "Parola";
      password.setAttribute("aria-label", "Parola");
      const submit = document.createElement("button");
      submit.className = "button primary";
      submit.textContent = "Giriş yap";
      form.append(email, password, submit);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submit.disabled = true;
        try {
          await unwrap(bridge.login(email.value, password.value));
          password.value = "";
          await status();
          show();
          location.hash = "workflows";
        } catch (e) {
          error.textContent = e.message;
        } finally {
          password.value = "";
          submit.disabled = false;
        }
      });
      content.append(form);
    } else {
      const state = document.createElement("p");
      const trial = current.trial?.trial || current.trial;
      state.textContent =
        trial?.state === "expired"
          ? "Demo tamamlandı. Lisans seçenekleri için yöneticinizle iletişime geçin."
          : trial?.state === "not_started"
            ? "Demo: Bir takvim ayı boyunca toplam üç akış kullanabilirsiniz."
            : trial?.expires_at
              ? `Kullanılan akış: ${trial.flows_used} / ${trial.flow_limit} · Bitiş: ${new Date(trial.expires_at).toLocaleDateString("tr-TR")}`
              : "Hesabınızın doğrulama ve yönetici onayını tamamlayın.";
      content.append(state);
      if (trial?.state === "not_started")
        action("Demo sürümünü başlat", () => bridge.startTrial());
      if (["active", "paid"].includes(trial?.state) && !current.licensed)
        action("Bu cihazı etkinleştir", () => bridge.activate());
      action("Oturumu kapat", () => bridge.logout());
    }
    action("Hesap sayfasını aç", () => bridge.openAccount());
    content.append(error);
    if (current?.user) {
      action("Çalışma klasörünü aç", () => bridge.openWorkspace());
      action("Akış verisi içe aktar", () => bridge.importFile());
    }
    if (!current?.secure_storage) {
      error.textContent = readable("DESKTOP_SECURE_STORAGE_REQUIRED");
    }
    if (!dialog.open) dialog.showModal();
  }
  document.addEventListener("DOMContentLoaded", async () => {
    document.querySelector(".environment-pill").textContent = "MASAÜSTÜ";
    document.querySelector(".workspace small").textContent =
      "Yerel çalışma alanı";
    document.querySelector(".sidebar-foot small").textContent =
      "İşler bu cihazda çalışır";
    const button = document.createElement("button");
    button.id = "desktop-account-button";
    button.className = "button small";
    button.type = "button";
    button.textContent = "Hesabım";
    button.addEventListener("click", async () => {
      await status();
      show();
    });
    document.querySelector(".topbar-right").prepend(button);
    const note = document.createElement("div");
    note.id = "desktop-license-note";
    note.className = "desktop-license-note";
    document.querySelector(".workspace-main").append(note);
    const dialog = document.createElement("dialog");
    dialog.id = "desktop-account-dialog";
    dialog.className = "desktop-account-dialog";
    dialog.innerHTML =
      '<button type="button" class="desktop-close" aria-label="Kapat">×</button><div class="desktop-dialog-body"></div>';
    dialog
      .querySelector("button")
      .addEventListener("click", () => dialog.close());
    document.body.append(dialog);
    // Local retries are explicit reconciliation operations; never automatic.
    new MutationObserver(() => {
      const field = document.querySelector("#flow-attempts");
      if (field) {
        field.value = "1";
        field.max = "1";
        field.closest(".field-group").hidden = true;
      }
    }).observe(document.querySelector("#form-dialog"), {
      childList: true,
      subtree: true,
    });
    await status();
    if (!current?.user) show();
    setInterval(() => status().catch(() => {}), 30000);
  });
})();
