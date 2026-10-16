import { busy, element } from "./portal-api.js";

/** @param {import('./portal-api.js').PortalApi} api @param {(error: unknown) => void} onError */
export function licenseManager(api, onError) {
  let account = null;
  const form = element("license-form");
  const operation = element("license-operation");
  function updateFields() {
    const grant = operation.value === "grant";
    element("license-grant-fields").hidden = !grant;
    element("license-expiry").required = grant;
    element("license-flow-limit").required = grant;
  }
  operation.addEventListener("change", updateFields);
  function close() {
    account = null;
    element("license-panel").hidden = true;
    form.reset();
  }
  async function refresh() {
    if (!account) return;
    const status = await api.request(`/api/admin/tenants/${encodeURIComponent(account.tenant_id)}/license`);
    const state = { not_started: "Demo başlamadı", active: "Etkin", expired: "Süre doldu", suspended: "Askıda", unapproved: "Onay bekliyor" }[status.state] || status.state;
    const end = status.expires_at ? new Date(status.expires_at).toLocaleString("tr-TR") : "—";
    element("license-status").textContent = `${state} · ${status.plan === "paid" ? "Ücretli lisans" : "Demo"} · ${status.flows_used}/${status.flow_limit} akış · Bitiş: ${end} · Bilgisayar: ${status.device_id ? "Bağlı" : "Bağlı değil"}`;
  }
  async function open(user) {
    account = user;
    form.reset();
    updateFields();
    element("license-account").textContent = `${user.display_name} · ${user.email}`;
    element("license-status").textContent = "Lisans durumu yükleniyor…";
    element("license-panel").hidden = false;
    await refresh();
    element("license-operation").focus();
  }
  form.addEventListener("submit", event => {
    event.preventDefault();
    void busy(form, async () => {
      if (!account) return;
      try {
        const data = new FormData(form);
        const action = String(data.get("operation"));
        const body = { reason: String(data.get("reason") || "").trim() };
        if (action === "grant") {
          body.expires_at = new Date(String(data.get("expires_at"))).toISOString();
          body.flow_limit = Number(data.get("flow_limit"));
        }
        const suffix = action === "reset" ? "device/reset" : `license/${action}`;
        await api.request(`/api/admin/tenants/${encodeURIComponent(account.tenant_id)}/${suffix}`, { method: "POST", authenticated: true, body });
        await refresh();
        element("license-reason").value = "";
      } catch (error) { onError(error); }
    });
  });
  element("close-license").addEventListener("click", close);
  return { open, close };
}
