"use strict";

const SecretUI = (() => {
  let records = [];
  let selected = null;
  const kinds = { http: "HTTP API", smtp: "E-posta", browser: "Tarayıcı girişi" };
  const input = (key, label, password = false, value = "") => `<div class="field-group"><label for="credential-${key}">${escapeHtml(label)}</label><input id="credential-${key}" name="${key}" type="${password ? "password" : "text"}" value="${escapeHtml(value)}" autocomplete="off" maxlength="4096" ${["username", "password"].includes(key) && document.querySelector('#credential-kind')?.value === 'smtp' ? "" : "required"}></div>`;
  function fields() {
    const kind = document.querySelector("#credential-kind").value;
    selected = selected ? { ...selected, kind } : null;
    document.querySelector("#credential-fields").innerHTML = kind === "http"
      ? input("authorization", "Authorization değeri (ör. Bearer …)", true)
      : kind === "browser" ? input("username", "Kullanıcı adı") + input("password", "Parola", true)
      : input("host", "SMTP sunucusu") + input("port", "SMTP portu", false, "587") + input("sender", "Gönderen e-posta") + '<div class="field-group"><label for="credential-security">Bağlantı</label><select id="credential-security" name="security"><option value="starttls">STARTTLS (587)</option><option value="ssl">SSL (465)</option></select></div>' + input("username", "SMTP kullanıcı adı") + input("password", "SMTP parolası", true);
  }
  function render() {
    document.querySelector("#dialog-content").innerHTML = `${dialogHeader("Bağlantı bilgileri", "Parola ve anahtarları burada saklayın; akışta yalnız bağlantı adını kullanın.")}<div class="dialog-body"><div class="credential-list">${records.map(row => `<div class="credential-item"><strong>${escapeHtml(row.reference)}</strong><span class="text-small">${escapeHtml(kinds[row.kind])} · v${row.version}</span><button class="button small" type="button" data-credential-edit="${escapeHtml(row.reference)}">Yenile</button><button class="button small" type="button" data-credential-delete="${escapeHtml(row.reference)}">Sil</button></div>`).join("") || '<p class="muted">Henüz bir bağlantı bilgisi eklemediniz.</p>'}</div><form id="credential-form"><div class="field-group"><label for="credential-reference">Bağlantı adı</label><input id="credential-reference" name="reference" pattern="[a-z][a-z0-9_-]{0,79}" maxlength="80" value="${escapeHtml(selected?.reference || "")}" ${selected ? "readonly" : ""} required><small>Örnek: muhasebe_api veya eposta. Değeri kaydettikten sonra okunmaz; yenilemek için yeniden girilir.</small></div><div class="field-group"><label for="credential-kind">Bağlantı türü</label><select id="credential-kind" name="kind" ${selected ? "disabled" : ""}>${Object.entries(kinds).map(([kind, label]) => `<option value="${kind}" ${kind === (selected?.kind || "http") ? "selected" : ""}>${label}</option>`).join("")}</select></div><div id="credential-fields"></div><div id="credential-error" class="form-error" role="alert"></div><button class="button primary" type="submit">${selected ? "Bağlantıyı yenile" : "Bağlantıyı kaydet"}</button></form></div>`;
    fields();
    document.querySelector("#credential-kind").addEventListener("change", fields);
  }
  async function open() {
    try {
      records = (await api("/api/secrets")).items;
      selected = null;
      render();
      formDialog.showModal();
    } catch (error) { toast(error.message, true); }
  }
  document.addEventListener("click", event => {
    if (event.target.closest('[data-action="secrets"]')) { event.preventDefault(); void open(); }
    const edit = event.target.closest("[data-credential-edit]");
    if (edit) { selected = records.find(row => row.reference === edit.dataset.credentialEdit); render(); }
    const remove = event.target.closest("[data-credential-delete]");
    if (remove) {
      const row = records.find(item => item.reference === remove.dataset.credentialDelete);
      if (!row || !window.confirm(`${row.reference} bağlantısını silmek istiyor musunuz?`)) return;
      remove.disabled = true;
      void api(`/api/secrets/${encodeURIComponent(row.reference)}`, { expected_version: row.version }, "DELETE")
        .then(async () => { records = (await api("/api/secrets")).items; selected = null; render(); })
        .catch(error => toast(error.message, true))
        .finally(() => { remove.disabled = false; });
    }
  });
  document.addEventListener("submit", event => {
    if (event.target.id !== "credential-form") return;
    event.preventDefault();
    const form = event.target;
    const submit = form.querySelector('[type="submit"]');
    if (submit.disabled) return;
    const values = Object.fromEntries(new FormData(form));
    const reference = values.reference;
    const kind = selected?.kind || values.kind;
    delete values.reference; delete values.kind;
    submit.disabled = true;
    const body = { kind, values, ...(selected ? { expected_version: selected.version } : {}) };
    void api(`/api/secrets/${encodeURIComponent(reference)}`, body, "PUT")
      .then(async () => { form.reset(); records = (await api("/api/secrets")).items; selected = null; render(); toast("Bağlantı bilgisi kaydedildi."); })
      .catch(error => { document.querySelector("#credential-error").textContent = error.message; })
      .finally(() => { submit.disabled = false; });
  });
  return { open };
})();
