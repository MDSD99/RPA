import { ApiError, PortalApi, busy, element, errorMessage } from "./portal-api.js";
import { licenseManager } from "./admin-license.js";

/** @typedef {import('./portal-api.js').Account} Account */
/** @typedef {import('./portal-api.js').Identity} Identity */
/** @typedef {import('./portal-api.js').AccountPage} AccountPage */
/** @typedef {import('./portal-api.js').Decision} Decision */

const api = new PortalApi();
const licenses = licenseManager(api, handleError);
const PAGE_SIZE = 50;
/** @type {Identity | null} */
let identity = null;
/** @type {AccountPage} */
let accounts = { items: [], total: 0, limit: PAGE_SIZE, offset: 0 };
/** @type {{user: Account, decision: Decision} | null} */
let selectedDecision = null;
let loadingAccounts = false;

/** @type {Record<Decision, {label: string, title: string, description: string}>} */
const decisions = {
  approve: { label: "Onayla", title: "Başvuruyu onayla", description: "Bu hesabın yönetici onayını tamamlayacaksınız. Deneme süresi bu işlemle başlamaz." },
  reject: { label: "Reddet", title: "Başvuruyu reddet", description: "Bu başvuru onaylanmayacak. E-posta doğrulama durumu korunur." },
  suspend: { label: "Askıya al", title: "Hesabı askıya al", description: "Bu hesabın erişimi askıya alınacak ve açık oturumları kapatılacak." },
  restore: { label: "Askıyı kaldır", title: "Hesabı yeniden etkinleştir", description: "Bu hesabın askı durumu kaldırılacak. E-posta doğrulaması ve başvuru kararı ayrıca korunur." },
};

/** @param {string} message @param {boolean} [error] */
function notice(message, error = false) {
  const node = element("notice");
  node.textContent = message;
  node.classList.toggle("notice-error", error);
  node.hidden = !message;
  if (message) node.focus();
}

function showLogin() {
  licenses.close();
  identity = null;
  api.clearSession();
  element("loading").hidden = true;
  element("admin-login-view").hidden = false;
  element("admin-accounts-view").hidden = true;
  element("accounts-body").replaceChildren();
  closeDecision();
}

/** @param {Identity} result */
function useIdentity(result) {
  identity = result;
  api.setCsrf(result.csrf_token);
  element("admin-identity").textContent = result.user.email;
  element("loading").hidden = true;
  element("admin-login-view").hidden = true;
  element("admin-accounts-view").hidden = false;
}

/** @param {unknown} error */
function handleError(error) {
  if (error instanceof ApiError && error.status === 401 && identity) showLogin();
  notice(errorMessage(error), true);
}

function updatePagination() {
  /** @type {HTMLButtonElement} */ (element("previous-page")).disabled = loadingAccounts || accounts.offset === 0;
  /** @type {HTMLButtonElement} */ (element("next-page")).disabled = loadingAccounts || accounts.offset + accounts.limit >= accounts.total;
  /** @type {HTMLButtonElement} */ (element("accounts-refresh-button")).disabled = loadingAccounts;
}

/** @param {number} offset */
async function loadAccounts(offset) {
  if (loadingAccounts) return;
  loadingAccounts = true;
  element("accounts-body").setAttribute("aria-busy", "true");
  updatePagination();
  try {
    /** @type {AccountPage} */
    const page = await api.request(`/api/admin/accounts?limit=${PAGE_SIZE}&offset=${Math.max(0, offset)}`);
    accounts = page;
    closeDecision();
    renderAccounts();
  } finally {
    loadingAccounts = false;
    element("accounts-body").removeAttribute("aria-busy");
    updatePagination();
  }
}

/** @param {string} text @param {string} [className] @returns {HTMLSpanElement} */
function span(text, className = "") {
  const node = document.createElement("span");
  node.textContent = text;
  node.className = className;
  return node;
}

/** @param {Account} user @returns {Decision[]} */
function availableDecisions(user) {
  if (user.is_admin) return [];
  if (user.suspended) return ["restore"];
  if (user.approval === "approved") return ["suspend"];
  const result = /** @type {Decision[]} */ ([]);
  if (user.email_verified) result.push("approve");
  if (user.approval === "pending") result.push("reject");
  return result;
}

function renderAccounts() {
  const body = element("accounts-body");
  const fragment = document.createDocumentFragment();
  for (const user of accounts.items) {
    const row = document.createElement("tr");
    const contact = document.createElement("td");
    contact.append(span(user.display_name, "account-name"), span(user.email, "account-contact"));
    const verification = document.createElement("td");
    verification.dataset.label = "E-posta";
    verification.append(span(user.email_verified ? "Doğrulandı" : "Bekliyor", user.email_verified ? "badge" : "badge badge-waiting"));
    const approval = document.createElement("td");
    approval.dataset.label = "Başvuru";
    const label = user.suspended ? "Askıda" : { pending: "Onay bekliyor", approved: "Onaylandı", rejected: "Reddedildi" }[user.approval];
    const badgeClass = user.suspended ? "badge-suspended" : { pending: "badge-waiting", approved: "", rejected: "badge-rejected" }[user.approval];
    approval.append(span(label, `badge ${badgeClass}`));
    const actions = document.createElement("td");
    const actionGroup = document.createElement("div");
    actionGroup.className = "account-actions";
    const options = availableDecisions(user);
    for (const decision of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = decisions[decision].label;
      button.setAttribute("aria-label", `${user.email}: ${decisions[decision].label}`);
      button.addEventListener("click", () => openDecision(user, decision));
      actionGroup.append(button);
    }
    if (options.length === 0) actionGroup.append(span(user.is_admin ? "Yönetici hesabı" : "Doğrulama bekleniyor", "account-contact"));
    if (!user.is_admin) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = "Lisans ve bilgisayar";
      button.setAttribute("aria-label", `${user.email}: Lisans ve bilgisayar`);
      button.addEventListener("click", () => { void licenses.open(user).catch(handleError); });
      actionGroup.append(button);
    }
    actions.append(actionGroup);
    row.append(contact, verification, approval, actions);
    fragment.append(row);
  }
  body.replaceChildren(fragment);
  element("empty-accounts").hidden = accounts.items.length !== 0;
  element("account-total").textContent = `${accounts.total} hesap · E-posta ve başvuru durumu ayrı değerlendirilir.`;
  const first = accounts.items.length ? accounts.offset + 1 : 0;
  const last = accounts.offset + accounts.items.length;
  element("page-summary").textContent = accounts.items.length ? `${first}–${last} / ${accounts.total} hesap` : "Gösterilecek hesap yok";
}

/** @param {Account} user @param {Decision} decision */
function openDecision(user, decision) {
  if (element("decision-form").getAttribute("aria-busy") === "true") return;
  selectedDecision = { user, decision };
  element("decision-title").textContent = decisions[decision].title;
  element("decision-account").textContent = `${user.display_name} · ${user.email}`;
  element("decision-description").textContent = decisions[decision].description;
  const confirm = element("confirm-decision");
  confirm.textContent = decisions[decision].label;
  confirm.classList.toggle("button-danger", decision === "reject" || decision === "suspend");
  /** @type {HTMLFormElement} */ (element("decision-form")).reset();
  element("decision-panel").hidden = false;
  notice("");
  element("decision-reason").focus();
}

function closeDecision() {
  selectedDecision = null;
  element("decision-panel").hidden = true;
  /** @type {HTMLFormElement} */ (element("decision-form")).reset();
}

const loginForm = /** @type {HTMLFormElement} */ (element("admin-login-form"));
loginForm.addEventListener("submit", event => {
  event.preventDefault();
  void busy(loginForm, async () => {
    notice("");
    const data = new FormData(loginForm);
    try {
      /** @type {Identity} */
      const result = await api.request("/api/admin/login", {
        method: "POST", body: { email: String(data.get("email") || ""), password: String(data.get("password") || ""), totp: String(data.get("totp") || "") },
      });
      loginForm.reset();
      useIdentity(result);
      await loadAccounts(0);
    } catch (error) { handleError(error); }
    finally { /** @type {HTMLInputElement} */ (element("admin-totp")).value = ""; }
  });
});

const decisionForm = /** @type {HTMLFormElement} */ (element("decision-form"));
decisionForm.addEventListener("submit", event => {
  event.preventDefault();
  void busy(decisionForm, async () => {
    if (!selectedDecision) return;
    const { user, decision } = selectedDecision;
    const reason = String(new FormData(decisionForm).get("reason") || "").trim();
    if (reason.length < 3) {
      notice("En az 3 karakterlik bir işlem gerekçesi yazın.", true);
      return;
    }
    try {
      await api.request(`/api/admin/accounts/${encodeURIComponent(user.id)}/decision`, { method: "POST", authenticated: true, body: { decision, reason } });
      closeDecision();
      notice("Hesap kararı kaydedildi.");
      await loadAccounts(accounts.offset);
    } catch (error) { handleError(error); }
  });
});

element("cancel-decision").addEventListener("click", () => {
  closeDecision();
  element("accounts-refresh-button").focus();
});

element("accounts-refresh-button").addEventListener("click", () => {
  notice("");
  void loadAccounts(accounts.offset).catch(handleError);
});
element("previous-page").addEventListener("click", () => { void loadAccounts(Math.max(0, accounts.offset - accounts.limit)).catch(handleError); });
element("next-page").addEventListener("click", () => { void loadAccounts(accounts.offset + accounts.limit).catch(handleError); });

element("admin-logout-button").addEventListener("click", () => {
  void busy(element("admin-logout-button"), async () => {
    try {
      await api.request("/api/admin/logout", { method: "POST", authenticated: true });
      showLogin();
      notice("Yönetici oturumunuz kapatıldı.");
    } catch (error) { handleError(error); }
  });
});

async function initialize() {
  try {
    /** @type {Identity} */
    const result = await api.request("/api/admin/me");
    useIdentity(result);
    await loadAccounts(0);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) showLogin();
    else {
      if (!identity) showLogin();
      handleError(error);
    }
  }
}

void initialize();
