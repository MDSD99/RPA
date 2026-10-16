/**
 * @typedef {'pending' | 'approved' | 'rejected'} Approval
 * @typedef {Object} Account
 * @property {string} id
 * @property {string} tenant_id
 * @property {string} email
 * @property {string} display_name
 * @property {boolean} email_verified
 * @property {Approval} approval
 * @property {boolean} suspended
 * @property {boolean} is_admin
 *
 * @typedef {{user: Account, csrf_token: string}} Identity
 * @typedef {{message: string}} MessageResponse
 * @typedef {{items: Account[], total: number, limit: number, offset: number}} AccountPage
 * @typedef {'approve' | 'reject' | 'suspend' | 'restore'} Decision
 */

/** An expected HTTP failure; no response body is logged. */
export class ApiError extends Error {
  /** @param {string} message @param {number} status @param {string} code */
  constructor(message, status, code) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** Same-origin JSON transport. CSRF values stay in this page's memory. */
export class PortalApi {
  #csrf = "";

  /** @param {string} token */
  setCsrf(token) { this.#csrf = token; }

  clearSession() { this.#csrf = ""; }

  /**
   * @template T
   * @param {string} path
   * @param {{method?: 'GET' | 'POST', body?: object, authenticated?: boolean}} [options]
   * @returns {Promise<T>}
   */
  async request(path, options = {}) {
    const method = options.method || "GET";
    const headers = new Headers({ Accept: "application/json" });
    if (method === "POST") {
      headers.set("Content-Type", "application/json");
      headers.set("X-RPA-Request", "1");
      if (options.authenticated && this.#csrf) {
        headers.set("X-CSRF-Token", this.#csrf);
      }
    }
    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error",
        signal: AbortSignal.timeout(15000),
        body: method === "POST" ? JSON.stringify(options.body || {}) : undefined,
      });
    } catch {
      throw new ApiError("Bağlantı kurulamadı. Bağlantınızı kontrol edip tekrar deneyin.", 0, "NETWORK_ERROR");
    }
    let payload;
    try {
      payload = await response.json();
    } catch {
      throw new ApiError("Sunucudan beklenen yanıt alınamadı. Lütfen tekrar deneyin.", response.status, "INVALID_RESPONSE");
    }
    if (!response.ok) {
      const code = typeof payload?.error?.code === "string" ? payload.error.code : "REQUEST_FAILED";
      const message = typeof payload?.error?.message === "string"
        ? payload.error.message
        : "İşlem tamamlanamadı. Bilgilerinizi kontrol edip tekrar deneyin.";
      throw new ApiError(message, response.status, code);
    }
    return /** @type {T} */ (payload);
  }
}

/** @template {HTMLElement} T @param {string} id @returns {T} */
export function element(id) {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing element: ${id}`);
  return /** @type {T} */ (found);
}

/** Prevent duplicate submission and keep keyboard/status feedback available. */
export async function busy(container, work) {
  if (container.getAttribute("aria-busy") === "true") return;
  const buttons = [...container.querySelectorAll("button")];
  if (container instanceof HTMLButtonElement) buttons.push(container);
  const wasDisabled = buttons.map(button => button.disabled);
  container.setAttribute("aria-busy", "true");
  buttons.forEach(button => { button.disabled = true; });
  try {
    await work();
  } finally {
    container.removeAttribute("aria-busy");
    buttons.forEach((button, index) => { button.disabled = wasDisabled[index]; });
  }
}

/** @param {unknown} error @returns {string} */
export function errorMessage(error) {
  return error instanceof ApiError ? error.message : "İşlem tamamlanamadı. Lütfen tekrar deneyin.";
}
