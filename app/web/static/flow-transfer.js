"use strict";

const FlowTransferUI = (() => {
  const MAX_FILE_BYTES = 128 * 1024;
  let exportPending = false;

  function preview(document) {
    if (
      !document ||
      document.format !== "multirpa-flow" ||
      (document.schema_version !== 1 && document.schema_version !== 2) ||
      !document.flow ||
      typeof document.flow.name !== "string" ||
      !Array.isArray(document.flow.graph?.nodes)
    ) {
      throw new Error("Bu dosya desteklenen bir MultiRPA akış dosyası değil.");
    }
    if (!FlowUI.supportsGraph(document.flow.graph)) {
      throw new Error("Bu akışın adımları bu MultiRPA sürümüyle desteklenmiyor. Uygulamayı güncelleyin.");
    }
    if (
      document.schema_version === 1 &&
      document.flow.graph.nodes.some((node) => Object.hasOwn(node, "type_version"))
    ) {
      throw new Error("Akış dosyasının biçimi adımlarıyla uyuşmuyor. Kaynak uygulamadan yeniden dışa aktarın.");
    }
    return document.flow;
  }

  function openImport() {
    const desktop = document.documentElement.dataset.workspace === "desktop";
    const quotaNote = desktop
      ? "Yeni bir yerel taslak oluşturulur. İlk çalıştırmada ortak akış kotanızdan bir hak kullanılır."
      : "Yeni bir akış oluşturulur. Demo kullanıyorsanız üç akış hakkınızdan biri kullanılır.";
    $("#dialog-content").innerHTML = `${dialogHeader("Akışı içe aktar", "Web veya masaüstünden dışa aktardığınız akış dosyasını seçin.")}
      <form id="flow-import-form">
        <div class="dialog-body">
          <div class="field-group">
            <label for="flow-import-file">Akış dosyası</label>
            <input id="flow-import-file" type="file" accept=".multirpa.json,.json,application/json" required>
            <small>MultiRPA JSON dosyası · En fazla 128 KB</small>
          </div>
          <div id="flow-import-preview" class="transfer-preview" hidden>
            <strong id="flow-import-name"></strong>
            <span id="flow-import-steps"></span>
          </div>
          <p class="inline-note">${quotaNote} Akış kendiliğinden çalıştırılmaz.</p>
          <p class="inline-note">Bağlantı kasasındaki parolalar ve çalıştırma geçmişi aktarılmaz. Bağlantı adlarını ve hedef adresleri çalıştırmadan önce kontrol edin.</p>
          <div id="flow-import-error" class="form-error" role="alert"></div>
        </div>
        <div class="dialog-footer">
          <button class="button" type="button" data-action="close-dialog">Vazgeç</button>
          <button class="button primary" type="submit" disabled>Akışı içe aktar</button>
        </div>
      </form>`;

    const form = $("#flow-import-form");
    const input = $("#flow-import-file");
    const submit = form.querySelector('[type="submit"]');
    const errorBox = $("#flow-import-error");
    const summary = $("#flow-import-preview");
    let archive = null;
    let reading = 0;
    let submitting = false;

    input.addEventListener("change", async () => {
      const ticket = ++reading;
      archive = null;
      submit.disabled = true;
      summary.hidden = true;
      errorBox.textContent = "";
      const file = input.files[0];
      if (!file) return;
      try {
        if (!file.size || file.size > MAX_FILE_BYTES) {
          throw new Error("Akış dosyası boş olamaz ve 128 KB sınırını aşamaz.");
        }
        const content = await file.text();
        if (ticket !== reading || !form.isConnected || !formDialog.open) return;
        if (new TextEncoder().encode(content).byteLength > MAX_FILE_BYTES) {
          throw new Error("Akış dosyası 128 KB sınırını aşıyor.");
        }
        let parsed;
        try {
          parsed = JSON.parse(content.replace(/^\uFEFF/, ""));
        } catch {
          throw new Error("Dosya okunamadı. Geçerli bir MultiRPA JSON dosyası seçin.");
        }
        const flow = preview(parsed);
        $("#flow-import-name").textContent = flow.name;
        $("#flow-import-steps").textContent = `${flow.graph.nodes.length} adım · Yeni akış kopyası`;
        summary.hidden = false;
        // Preserve numeric tokens for authoritative Python validation. Parsing
        // and reserializing here would silently accept versions such as 1.0.
        archive = content;
        submit.disabled = false;
      } catch (error) {
        if (ticket === reading && form.isConnected) errorBox.textContent = error.message;
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!archive || submitting) return;
      submitting = true;
      const originRoute = location.hash;
      submit.disabled = true;
      input.disabled = true;
      errorBox.textContent = "";
      try {
        const result = await api("/api/flows/import", { document: archive });
        const stillHere = form.isConnected && formDialog.open && location.hash === originRoute;
        if (form.isConnected && formDialog.open) formDialog.close();
        toast("Akış yeni bir kopya olarak içe aktarıldı. Çalıştırmadan önce adımlarını kontrol edin.");
        if (stillHere) location.hash = `flows/${result.flow.id}`;
      } catch (error) {
        if (form.isConnected) errorBox.textContent = error.message;
        else toast(error.message, true);
      } finally {
        submitting = false;
        submit.disabled = !archive;
        input.disabled = false;
      }
    });
    formDialog.showModal();
  }

  async function exportFlow(flowId) {
    if (exportPending) return;
    exportPending = true;
    try {
      if (window.multirpaDesktop) {
        const response = await window.multirpaDesktop.exportFlow(flowId);
        if (!response.ok) throw new Error("Akış dosyası kaydedilemedi. Tekrar deneyin.");
        if (response.data.cancelled) return;
      } else {
        const result = await api(`/api/flows/${flowId}/export`);
        if (!/^MultiRPA-flow-[a-f0-9-]{36}-v[1-9][0-9]*\.multirpa\.json$/.test(result.filename)) {
          throw new Error("Akış dosyasının adı doğrulanamadı.");
        }
        preview(result.document);
        const content = JSON.stringify(result.document) + "\n";
        if (new TextEncoder().encode(content).byteLength > MAX_FILE_BYTES) {
          throw new Error("Akış dosyası 128 KB sınırını aşıyor.");
        }
        const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = result.filename;
        document.body.append(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
      toast("Kaydedilmiş akış sürümü dışa aktarıldı. Akışa yazdığınız sabit metinler de dosyada yer alır.");
    } catch (error) {
      toast(error.message, true);
    } finally {
      exportPending = false;
    }
  }

  return Object.freeze({ openImport, exportFlow });
})();
