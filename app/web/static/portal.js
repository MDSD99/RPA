import { ApiError, PortalApi, busy, element, errorMessage } from "./portal-api.js";

/** @typedef {import('./portal-api.js').Account} Account */
/** @typedef {import('./portal-api.js').Identity} Identity */
/** @typedef {import('./portal-api.js').MessageResponse} MessageResponse */
/** @typedef {'login' | 'register' | 'verification-request' | 'verify' | 'password-request' | 'password-reset' | 'status'} View */

const api = new PortalApi();
/** @type {Account | null} */
let account = null;
let linkToken = "";
const knownViews = new Set(["login", "register", "verification-request", "verify", "password-request", "password-reset", "status"]);

/** @param {string} message @param {boolean} [error] */
function notice(message, error = false) {
  const node = element("notice");
  node.textContent = message;
  node.classList.toggle("notice-error", error);
  node.hidden = !message;
  if (message) node.focus();
}

/** @param {View} name @param {boolean} [clearNotice] */
function showView(name, clearNotice = true) {
  document.querySelectorAll(".auth-view").forEach(view => {
    if (view instanceof HTMLElement) view.hidden = view.id !== `${name}-view`;
  });
  element("loading").hidden = true;
  if (clearNotice) notice("");
  const heading = element(`${name}-view`).querySelector("h2");
  if (heading) document.title = `${heading.textContent} · ${document.documentElement.dataset.productName}`;
}

/** @param {HTMLFormElement} form @param {string} name @returns {string} */
function field(form, name) {
  return String(new FormData(form).get(name) || "");
}

/** @param {Identity} identity */
function useIdentity(identity) {
  account = identity.user;
  api.setCsrf(identity.csrf_token);
  renderStatus(identity.user);
  void loadTrial().catch(handleError);
}

/** @param {Account} user */
function renderStatus(user) {
  element("trial-panel").hidden = true;
  element("downloads-panel").hidden = true;
  element("status-title").textContent = `Hoş geldiniz, ${user.display_name}.`;
  element("account-email").textContent = user.email;
  element("email-progress").classList.toggle("is-complete", user.email_verified);
  element("email-progress-label").textContent = user.email_verified ? "Tamamlandı" : "Bekliyor";
  element("approval-progress").classList.toggle("is-complete", user.approval === "approved" && !user.suspended);
  element("approval-progress-label").textContent = user.suspended ? "Askıda" : { pending: "Bekliyor", approved: "Onaylandı", rejected: "Reddedildi" }[user.approval];
  element("resend-button").hidden = user.email_verified || user.suspended;
  let label, title, copy;
  if (user.suspended) {
    label = "Hesap askıya alındı";
    title = "Hesabınız şu anda kullanıma kapalı.";
    copy = "Yönetici hesabınızı askıya aldı. Durum değiştiğinde bu ekrandan görebilirsiniz.";
  } else if (user.approval === "rejected") {
    label = "Başvuru onaylanmadı";
    title = "Başvurunuz kabul edilmedi.";
    copy = "Yönetici başvurunuzu değerlendirdi ve onaylamadı. Yeni bir değerlendirme yapılırsa güncel durum burada görünür.";
  } else if (!user.email_verified) {
    label = "E-posta doğrulaması bekleniyor";
    title = "Gelen kutunuzu kontrol edin.";
    copy = "Size gönderilen bağlantıyı açıp e-posta adresinizi doğrulayın. İletiyi bulamıyorsanız istenmeyen posta klasörüne de bakın.";
  } else if (user.approval === "pending") {
    label = "Yönetici onayı bekleniyor";
    title = "E-postanız doğrulandı. Sırada onay var.";
    copy = "Başvurunuz yönetici tarafından değerlendirilecek. Onay durumu güncellendiğinde hesabınızda görebilirsiniz.";
  } else {
    label = "Hesabınız onaylandı";
    title = "Hesabınızın onay süreci tamamlandı.";
    copy = "Demo sürümünüzü başlatıp iş akışlarınızı oluşturmaya geçebilirsiniz.";
  }
  element("account-state-label").textContent = label;
  element("account-state-title").textContent = title;
  element("account-state-copy").textContent = copy;
  showView("status");
}

async function loadTrial() {
  const panel = element("trial-panel");
  panel.hidden = !account || !account.email_verified || account.approval !== "approved" || account.suspended;
  if (panel.hidden) return;
  void loadDownloads().catch(handleError);
  const trial = await api.request("/api/trial");
  element("start-trial-button").hidden = trial.state !== "not_started";
  element("workspace-link").hidden = trial.state === "not_started";
  element("trial-label").textContent = trial.plan === "paid" ? "Lisans" : "Demo";
  const expiry = trial.expires_at ? new Date(trial.expires_at).toLocaleString("tr-TR", { dateStyle: "long", timeStyle: "short" }) : "";
  element("trial-title").textContent = { not_started: "Bir aylık demo sürümünüz hazır.", active: trial.plan === "paid" ? "Lisansınız etkin." : "Demo sürümünüz etkin.", expired: trial.plan === "paid" ? "Lisansınız sona erdi." : "Demo tamamlandı." }[trial.state] || "Hesap kullanım durumu";
  element("trial-copy").textContent = trial.state === "not_started"
    ? "Başlattığınız andan itibaren bir takvim ayı boyunca toplam 3 farklı akış kullanabilirsiniz. Web ve masaüstü aynı sınırı paylaşır."
    : trial.state === "expired" ? "Yeni çalıştırmalar kapatıldı. Lisans için yöneticinizle iletişime geçin. Akışlarınıza ve geçmişinize çalışma alanından bakabilirsiniz."
    : `Kullanım hakkınız ${expiry} tarihinde sona erer.`;
  element("trial-quota").textContent = `${trial.flows_used} / ${trial.flow_limit} akış kullanıldı. Akış silmek veya uygulamayı yeniden kurmak bu sayacı sıfırlamaz.`;
}

async function loadDownloads() {
  const downloads = await api.request("/api/downloads");
  const panel = element("downloads-panel");
  panel.hidden = false;
  const list = element("downloads-list");
  list.replaceChildren();
  element("downloads-empty").hidden = downloads.items.length > 0;
  for (const item of downloads.items) {
    const link = document.createElement("a");
    link.href = item.url;
    link.className = "button";
    link.textContent = `${item.platform === "windows" ? "Windows" : "Linux"} için indir · ${item.version}`;
    list.append(link);
  }
}

element("start-trial-button").addEventListener("click", () => {
  void busy(element("start-trial-button"), async () => {
    try {
      await api.request("/api/trial/start", { method: "POST", authenticated: true, body: {} });
      await loadTrial();
      notice("Demo sürümünüz başladı. Çalışma alanınızı açabilirsiniz.");
    } catch (error) { handleError(error); }
  });
});

/** @param {unknown} error */
function handleError(error) {
  if (error instanceof ApiError && error.status === 401 && account) {
    account = null;
    api.clearSession();
    showView("login");
  }
  notice(errorMessage(error), true);
}

/** @param {string} id @param {(form: HTMLFormElement) => Promise<void>} submit */
function bindForm(id, submit) {
  const form = /** @type {HTMLFormElement} */ (element(id));
  form.addEventListener("submit", event => {
    event.preventDefault();
    void busy(form, async () => {
      notice("");
      try { await submit(form); } catch (error) { handleError(error); }
    });
  });
}

document.querySelectorAll("[data-view]").forEach(button => {
  button.addEventListener("click", () => {
    const view = button instanceof HTMLElement ? button.dataset.view : undefined;
    if (view && knownViews.has(view)) showView(/** @type {View} */ (view));
  });
});

bindForm("login-form", async form => {
  /** @type {Identity} */
  const identity = await api.request("/api/auth/login", {
    method: "POST", body: { email: field(form, "email"), password: field(form, "password") },
  });
  form.reset();
  useIdentity(identity);
});

bindForm("register-form", async form => {
  const email = field(form, "email");
  /** @type {MessageResponse} */
  const result = await api.request("/api/auth/register", {
    method: "POST", body: { email, password: field(form, "password"), display_name: field(form, "display_name") },
  });
  form.reset();
  /** @type {HTMLInputElement} */ (element("login-email")).value = email;
  showView("login");
  notice(result.message);
});

bindForm("verification-request-form", async form => {
  /** @type {MessageResponse} */
  const result = await api.request("/api/auth/verification/request", { method: "POST", body: { email: field(form, "email") } });
  notice(result.message);
});

bindForm("password-request-form", async form => {
  /** @type {MessageResponse} */
  const result = await api.request("/api/auth/password/request", { method: "POST", body: { email: field(form, "email") } });
  notice(result.message);
});

bindForm("verify-form", async () => {
  if (!linkToken) {
    showView("verification-request");
    notice("Doğrulama bağlantısı eksik. Yeni bir bağlantı isteyin.", true);
    return;
  }
  /** @type {MessageResponse} */
  const result = await api.request("/api/auth/verification/confirm", { method: "POST", body: { token: linkToken } });
  linkToken = "";
  showView("login");
  notice(result.message);
});

bindForm("password-reset-form", async form => {
  if (!linkToken) {
    showView("password-request");
    notice("Parola yenileme bağlantısı eksik. Yeni bir bağlantı isteyin.", true);
    return;
  }
  /** @type {MessageResponse} */
  const result = await api.request("/api/auth/password/reset", { method: "POST", body: { token: linkToken, password: field(form, "password") } });
  linkToken = "";
  account = null;
  api.clearSession();
  form.reset();
  showView("login");
  notice(result.message);
});

element("logout-button").addEventListener("click", () => {
  void busy(element("logout-button"), async () => {
    try {
      await api.request("/api/auth/logout", { method: "POST", authenticated: true });
      account = null;
      api.clearSession();
      showView("login");
      notice("Oturumunuz kapatıldı.");
    } catch (error) { handleError(error); }
  });
});

element("refresh-button").addEventListener("click", () => {
  void busy(element("refresh-button"), async () => {
    try {
      useIdentity(await api.request("/api/auth/me"));
      notice("Hesap durumunuz güncellendi.");
    } catch (error) { handleError(error); }
  });
});

element("resend-button").addEventListener("click", () => {
  void busy(element("resend-button"), async () => {
    if (!account) return;
    try {
      /** @type {MessageResponse} */
      const result = await api.request("/api/auth/verification/request", { method: "POST", body: { email: account.email } });
      notice(result.message);
    } catch (error) { handleError(error); }
  });
});

async function initialize() {
  const path = window.location.pathname.replace(/\/$/, "");
  if (path === "/verify-email" || path === "/reset-password") {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("token") || "";
    linkToken = token.length <= 512 ? token : "";
    window.history.replaceState(null, "", window.location.pathname);
    if (path === "/verify-email") {
      showView(linkToken ? "verify" : "verification-request");
    } else {
      showView(linkToken ? "password-reset" : "password-request");
    }
    if (!linkToken) notice("Bağlantı eksik veya geçersiz. E-posta adresinizle yeni bir bağlantı isteyin.", true);
    return;
  }
  try {
    useIdentity(await api.request("/api/auth/me"));
  } catch (error) {
    const mode = new URLSearchParams(window.location.search).get("mode");
    showView(mode === "register" ? "register" : "login");
    if (!(error instanceof ApiError && error.status === 401)) handleError(error);
  }
}

void initialize();
