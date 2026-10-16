# MultiRPA masaüstü uygulaması

Güncelleme: 13 Ekim 2026. 12 Ekim Linux aktarım paketi ve gerçek yerel runner doğrulandı.
Windows yükleyicisi bu Linux sunucusunda üretilmiş veya Windows üzerinde denenmiş sayılmaz.

## Çalışan yapı

Electron **44.5.1**, Node **24.21.0 LTS** ve kilitli npm bağımlılıkları kullanılır.
Web ve masaüstü aynı `index.html`, `app.js`, `flows.js`, aktarım/kasa arayüzleri ve tasarım
dosyalarını kullanır. `prepare-renderer.cjs` bunları paket içine kopyalar; ayrı
bir masaüstü flow editörü geliştirilmez. Masaüstü, aynı API istemcisini dar
`multirpaTransport` köprüsüyle yerel işleme bağlar.

Renderer'da Node erişimi kapalı; `contextIsolation`, sandbox ve `webSecurity`
açıktır. Uzak sayfa yükleme, başka pencere açma, renderer ağ çağrısı ve işletim
sistemi izinleri engellenir. Preload yalnız belirli işlemler sunar. Main her IPC
çağrısının kendi ana frame'inden geldiğini ve yol/metot/boyut sınırlarını denetler.
Keyfi shell, dosya yolu veya URL çalıştırma köprüsü yoktur.

Main özel bir Python child process başlatır. İletişim stdin/stdout üzerinden
sınırlı JSON satırlarıyla yapılır; HTTP sunucusu veya dinleyen yerel port açılmaz.
Paketli runner Python kurulumunu kullanıcıdan istemez. PostgreSQL bağlantı
bilgileri, sunucu imzalama anahtarı ve müşteri flow içeriği merkezden alınmaz.

Flow, sabit sürümlü çalışma girdisi, sonuç ve checkpoint cihazdaki hesap başına
SQLite dosyasında tutulur. Motor, web worker'larının kullandığı `execute_graph`
fonksiyonudur. Dosya/CSV/Excel işlemleri hesabın özel `files` klasörüne bağlıdır.
Hesabım ekranındaki veri dosyası içe aktarma, kullanıcının işletim sistemi dosya seçicisinde seçtiği,
1 MiB'ı aşmayan CSV/XLSX/TXT/JSON dosyasını bu klasöre kopyalar. Yerel klasör
Hesabım ekranından açılabilir. Tarayıcı, HTTP ve SMTP hedefleri pakette operatörün
onayladığı alan adlarıyla sınırlandırılır.

Çalışmalar tek denemeyle başlar. İşlem kesilirse otomatik tekrar yapılmaz;
belirsiz dış sonuç inceleme gerektirir. Lisans/iptal kontrolü her yeni adımdan,
checkpoint'ten ve dış etkiden önce uygulanır. Gönderilmiş bir dış işlemin geri
alındığı varsayılmaz. Aynı `request_id` aynı işi döndürür; değiştirilmiş girdiler
aynı kimlikle yeni iş başlatamaz.

## Akış tanımını içe ve dışa aktarma

5 Ekim'de ortak editöre sürümlü `.multirpa.json` aktarımı eklendi.
**İş akışları → İçe aktar** en fazla 128 KiB dosyayı önizler ve ayrı oluşturma
adımıyla yeni yerel UUID/sürüm 1 kaydeder. Aynı dosyayı tekrar almak mevcut
akışı ezmez. İçe aktarma kendiliğinden çalıştırma veya zamanlama yapmaz;
yerel taslağın merkezi kota hakkı ilk çalıştırmadan önce ayrılır.

**Dışa aktar**, kaydedilmiş ve değişiklik beklemeyen akışta açıktır. Electron
native kaydetme penceresini açar; pencereyi iptal etmek dosya oluşturmaz.
Renderer yalnız akış kimliğini iletir; main, tanımı yerel runner'dan alır.
Dosya yolunu işletim sistemi penceresi belirler; renderer'a genel dosya yazma
yetkisi verilmez. İşlem sunucuya akış içeriği göndermez. Daha önce bu kurulumda
açılmış hesabın yerel tanımları çevrimdışı veya lisans süresi bittikten sonra da
aktarılabilir; çalıştırma için merkezi izin gerekliliği değişmez.

Tanım dosyası kasa referans adlarını ve akışa yazılmış sabit metinleri taşır.
Kasa parolaları, çalışma geçmişi, cihaz/lisans bilgileri ve `files` klasöründeki
belgeler eklenmez. Belgeleri çalışma klasörüne alma işlemi ayrı **veri dosyası**
içe aktarmasıdır. Hedef kurulumda gerekli kasa bağlantıları ayrıca oluşturulur;
izinli hedef ayarları taşınan akış tarafından değiştirilemez.

Tam dosya biçimi, API ve kota kuralları
[Flow Studio rehberinde](FLOW_STUDIO.md#akışı-dosyayla-taşıma) yer alır.

### Düğüm ve arşiv sürümü geçişi

13 Ekim düğüm sürümü uygulaması ve paket doğrulaması sürüyor. Yeni ortak
editör düğümleri açık `type_version: 1` taşır; eski alansız düğüm kalıcı v1
kalır. Eski akışı açmak veya dışa aktarmak JSON grafiğini dönüştürmez.
Arşiv v1 yalnız alansız eski grafik içindir; v2 açık sürümlü veya karma
grafiği taşır. Dışa aktarma uygun arşiv sürümünü kendisi seçer.

Bu nedenle 12 Ekim doğrulanmış arşivi 13 Ekim kaynak değişiklikleriyle aynı
paket sayılmaz. Yeni kaynaklar için native runner/Electron yeniden üretimi,
arşiv kaynak eşleşmesi ve gerçek paket aktarım denemesi ayrıca kaydedilir.
V2 dosya desteği olmayan bir kurulumda uygulama güncellenmelidir; dosyadaki
sürüm alanlarını silmek desteklenen bir geçiş yolu değildir.

Katalog desteği gerçek platform kabulünden ayrıdır. Windows türlerinin
şemada tanınması Linux'ta çalıştığı veya Windows UIA/DPAPI kabulünün geçtiği
anlamına gelmez. Ayrıntı [FLOW_CONTRACT.md](FLOW_CONTRACT.md) içinde.

## Hesap, demo ve tek cihaz

Kullanıcı web hesabıyla giriş yapar. E-posta doğrulama ve yönetici onayı merkezi
kurallardır. Demo açıkça başlatılır; bir takvim ayı ve web/masaüstünde ortak
toplam üç flow kimliği sunucuda tutulur. Akış silmek veya uygulamayı tekrar
kurmak süreyi/kotayı sıfırlamaz. Taslak düzenlemek çevrimdışı mümkündür; yeni
çalıştırma önce merkezi kota kaydı ve geçerli cihaz izni ister.

Cihaz Ed25519 anahtarıyla sunucunun tek kullanımlık challenge metnini imzalar.
Hesapta bir aktif cihaz anahtarı bulunur. IP adresi cihaz kimliği sayılmaz.
Anahtar Electron `safeStorage` ile işletim sistemi kasasında korunur. Linux'ta
güvenli secret store yoksa veya `basic_text` seçilmişse etkinleştirme engellenir;
düz metin fallback kullanılmaz. Windows DPAPI davranışı gerçek Windows kabul
testinde doğrulanmalıdır. Yerel yönetici veya değiştirilmiş uygulamaya karşı
donanım attestation/TPM güvencesi sunulmuş değildir.

Önceden bu kurulumda açılmış hesabın yerel akışları anahtar deposu kilitliyken
okunup düzenlenebilir. Runner bu durumda çalışma izni olmayan ayrı bir public
key ile başlar. Giriş, cihaz etkinleştirme, sır kasası ve akış çalıştırma kapalıdır;
ilk kurulumda hesap açılmadan yalnız kaydedilmemiş taslak hazırlanabilir.

Sunucu izni en fazla 60 saniyeliktir ve demo/lisans bitişini aşmaz. Main 25
saniyede bir yeniler. İmza önce paket içine sabitlenmiş sunucu public key'iyle
doğrulanır. Runner imzayı ayrıca doğrular; tenant, cihaz anahtarı özeti, flow
kimliği ve zaman sınırlarını denetler. Sunucunun imzaladığı `valid_for_ms`
alanından challenge ve yanıt süresi çıkarılır, süre monotonik saatle ölçülür.
Aynı lease tekrar yüklenerek süre uzatılamaz; izin diskte saklanmaz. Yenileme
başarısızlığı, uyku/uyanma ve süre bitişi yeni dış işlemi durdurur.

Yönetici cihaz değiştirirse önceki izin bitene kadar yeni cihaz çalışmaz.
Özel anahtarını kaybeden kurulum kendiliğinden yeni cihaz hakkı alamaz;
yönetici cihaz değişimi gerekir. Demo tamamlandığında hesap ekranı durumu
gösterir ve runner yürütmeyi engeller.

Yerel HTTP/SMTP kimlik bilgileri de hesap başına `safeStorage` ile şifrelenir.
`/api/secrets` köprüsü yalnız metadata döndürür; plaintext okuma yolu yoktur.
Sır değeri yalnız kullanıcı oluştururken/güncellerken renderer'dan main'e,
yürütme sırasında özel child process belleğine geçer. Sunucuya gönderilmez ve
SQLite flow/checkpoint içine yazılmaz.
Silinen sırın plaintext değeri kaldırılır; şifreli kasada sürüm tombstone'u
kalır. Aynı referans yeniden oluşturulduğunda sürüm artar. Etkin referansta
güncelleme/silme güncel `expected_version` ister; tür değişikliği reddedilir.
Yerel runner sır girdisini sunucuyla ortak credential şemasıyla doğrular.

## Linux geliştirme paketi

Bu sunucuda Node `desktop/.tools` altında, ek derleme/görüntüleme araçları da
proje içine açılmıştır; sistem paket kurulumu yapılmamıştır. Üretim Linux
masaüstünde normal GUI/GTK kütüphaneleri ve güvenli secret store gerekir.

```bash
export PATH=/opt/rpa/desktop/.tools/bin:/opt/rpa/desktop/.tools/node-v24.21.0-linux-x64/bin:$PATH
export LD_LIBRARY_PATH=/opt/rpa/desktop/.tools/system/usr/lib/x86_64-linux-gnu
export MULTIRPA_DESKTOP_SERVER_URL=http://127.0.0.1:8770
export MULTIRPA_DESKTOP_SERVER_PUBLIC_KEY=<sunucu-public-key>
.venv/bin/python -m pip install -r desktop/requirements-runtime.txt -r desktop/requirements-build.txt
PLAYWRIGHT_BROWSERS_PATH=/opt/rpa/.browser-cache .venv/bin/python -m playwright install chromium --only-shell
.venv/bin/python desktop/scripts/build-runner.py
.venv/bin/python desktop/scripts/smoke-runner.py
npm --prefix desktop ci
node desktop/node_modules/electron/install.js
npm --prefix desktop test
npm --prefix desktop run build:linux
.venv/bin/python desktop/scripts/verify-linux-release.py
node desktop/scripts/publish-release.cjs linux desktop/release/multirpa-desktop-0.1.0.tar.gz
```

Geliştirme sunucusu pin'i ile üretilen arşiv müşteri production paketi değildir.
`publish-release.cjs` arşivi `.runtime/releases` içine alır, SHA-256/boyut/
sürüm/platform metadata'sını oluşturur. Portal yalnız onaylanmış hesaplara bu
dosyayı indirir. Yerel flow JSON veya sırlar indirme servisine gitmez.
Gerçek müşteri paketi için HTTPS origin, doğru public key ve
`MULTIRPA_DESKTOP_RELEASE=1` gerekir. Sunucu private key'i pakete yazılmaz.
Production derleme/yayın doğrulaması HTTP, loopback, IP adresi ve ayrılmış
test origin'lerini reddeder; HTTPS `localhost` customer release sayılmaz.

İzinli hedefler build sırasında ayrı tanımlanır:
`MULTIRPA_DESKTOP_BROWSER_HOSTS`, `MULTIRPA_DESKTOP_HTTP_HOSTS` ve
`MULTIRPA_DESKTOP_MAIL_HOSTS` virgülle ayrılmış tam alan adlarıdır. Boş liste o
adapter'ın dış ağ erişimini kapatır. Flow belgesi bu izinleri değiştiremez.

## Windows derlemesi ve kabul

PyInstaller çıktısı işletim sistemine özgüdür. Windows runner Windows build
ortamında üretilir; Linux runner'ı `.exe` olarak adlandırmak kullanılmaz.
`desktop/scripts/build-windows.ps1` Python 3.12, Node 24.21.0 LTS ve npm kurulu
Windows build ortamında kilitli runtime/build bağımlılıkları, yerel Chromium,
runner, protokol testleri ve NSIS yükleyicisini sırayla üretir:

```powershell
./desktop/scripts/build-windows.ps1 `
  -ServerUrl 'https://app.<gerçek-alan-adı>' `
  -ServerPublicKey '<sunucu-public-key>' `
  -BrowserHosts 'müşteri-izinli-hedef.example' `
  -HttpHosts 'api-izinli-hedef.example' `
  -MailHosts 'smtp-izinli-hedef.example'
```

Signing sertifikası `CSC_LINK`/`CSC_KEY_PASSWORD` build secret store'undan
veya Windows certificate store için `CSC_NAME` ile sağlanır. Script Node
sürümünü ve sertifika ayarını denetler; native runner smoke testini çalıştırır.
Production origin/public key/izinli host'lar tanımlandıktan sonra ortak renderer
yeniden hazırlanır; eski geliştirme pin'i yükleyiciye kopyalanmaz.
Script otomatik yayın yapmaz. Bu sunucuda sertifika, gerçek alan adı
ve Windows build runner sağlanmadığı için imzalı Windows yükleyicisi yoktur.
Windows UIA click/fill/extract adapter'ı sadece Windows ve `pywinauto` varsa
çalışabilir; gerçek masaüstü kontrolü Linux testinden geçmiş sayılmaz.

Windows kabulü kurulum/kaldırma/yeniden kurulum, farklı cihaz aktivasyonunun
reddi, yönetici cihaz değişimi, ağ kesintisi, saat geri alma, uyku/uyanma,
demo bitişinde devam eden iş, DPAPI kullanıcı/makine sınırı ve gerçek UIA hedefi
senaryolarını içerir. Kilitli ekran/UI session sınırı ayrıca ölçülür.

Windows CI işi temiz bir Windows build ortamında Python 3.12 ve Node
24.21.0 kurar, sertifika ve production public ayarlarını secret store'dan alır,
bu script'i çalıştırır ve `desktop/release` yükleyicisini özel build artifact
olarak saklar. Gerçek UIA/DPAPI kabul işi ayrıca oturum açık Windows test
makinesi gerektirir; yükleyici üretilmesi bu kabulün yerine geçmez.

## Kanıtlar

3 Ekim 2026'da 16 Python runner/lease testi, 5 Node IPC/vault/serverclient testi,
Ruff ve beş desktop Python modülünün mypy kontrolü geçti. Gerçek paketli Linux
runner imzalı izinle dosya/CSV/Excel akışını tamamladı. Electron 44.5.1 bu
sunucuda proje içi Xvfb ile açıldı; aynı editor görüntülendi, schedule gizlendi
ve renderer'da `require` erişiminin bulunmadığı doğrulandı.

Görüntüler `artifacts/multirpa-desktop-login-2026-10-03.png` ve
`artifacts/multirpa-desktop-editor-2026-10-03.png` altında. Root olarak yapılan
bu görüntüleme denemesi CLI `--no-sandbox` istisnası kullanır; müşteri
uygulamasında bu bayrak eklenmez. Gerçek Windows kurulumu, OS key store ve
imzalı müşteri release'i bu kanıtlara dahil değildir.

4 Ekim 2026'da yenilenen paket **18 Python runner testi**, **6 Node testi**,
Ruff, Prettier ve beş desktop Python modülünün mypy kontrolünden geçti.
Gerçek arşiv içindeki ASAR'ın 32 ortak UI/desktop/template/pin dosyası ve
native runner'ın 26 app bytecode modülü güncel kaynakla eşleşti. SMTP adapter'ı
ve ortak sır şeması pakette bulunuyor; merkezi DB/API paketleri bulunmuyor.
Arşivden çıkarılmış runner imzalı cihaz izniyle dosya/CSV/Excel akışını tamamladı.
Arşivden açılan Electron aynı editörü görüntüledi; Node/process erişimi,
renderer ağı ve genel iş IPC çağrıları engellendi; güvenli kasa olmayan Linux
oturumunda girişin kapalı kaldığı doğrulandı.

4 Ekim görüntüleri `artifacts/multirpa-desktop-login-2026-10-04.png` ve
`artifacts/multirpa-desktop-editor-2026-10-04.png`; tekrar çalıştırma araçları
`verify-linux-release.py`, `smoke-runner.py --runner <paket-runner-yolu>` ve
`smoke-electron-linux.py --app <paket-Electron-yolu>` altında. GUI kontrolü
yalnız proje içine açılmış Xvfb/GTK araçlarını ve geçici kullanıcı profilini
kullanır. Sunucudaki `ELECTRON_RUN_AS_NODE` test ortamı değişkeni GUI child
process'inden çıkarılır; müşteri uygulamasına özel istisna eklenmez.

4 Ekim'de portal için hazırlanan geliştirme arşivi:
`MultiRPA-0.1.0-linux-x64.tar.gz`, **310352105 byte**,
SHA-256 `1860a1c07fd7e63ae00caf6d2fb6e0216285f5f84f2ced4192e95a95ae5e5009`.
Bu tarihsel paket gerçek production origin/key yerine yerel portal pin'iyle
üretilmiştir; sonraki paket bilgisi aşağıdadır.

12 Ekim'de aktarım teslimi için **12 Node testi**, Ruff/Prettier ve bütün
sunucu/tarayıcı testlerini içeren **854 passed, 2 warnings in 180.78s** tam
koşum geçti. Gerçek arşivdeki ASAR 34 ortak UI/desktop/template/pin kaynağıyla,
native runner 27 Python app modülüyle eşleşti. Arşivden açılan Electron dosya
önizlemesiyle yeni çevrimdışı taslak oluşturup tanımı dışa aktardı; bu işlem
iş, dosya yan etkisi veya lisans izni oluşturmadı. Paketli runner ayrıca
imzalı izinle dosya/CSV/Excel akışını tamamladı. Renderer'da Node/process,
ağ ve genel iş IPC engelleri korundu.

12 Ekim geliştirme arşivi `MultiRPA-0.1.0-linux-x64.tar.gz`, **310356572 byte**,
SHA-256 `0cf83d7ed909b9c91deb12d506e4de61b8ddf8f7470cd5b37b87b445eeed7eb4`.
Manifest `.runtime/releases/manifest.json` içinde development kanalına stage
edildi ve doğrulanmış arşivle eşleşti. Bu, 13 Ekim yeni düğüm sürümü kodunun
paketlendiği anlamına gelmez; yeni paket kanıtı ayrıca tutulur.

Kayıtlar `artifacts/multirpa-desktop-archive-verify-2026-10-12.txt`,
`artifacts/multirpa-desktop-archive-flow-transfer-smoke-2026-10-12.txt`,
`artifacts/multirpa-desktop-archive-runner-smoke-2026-10-12.txt` ve
`artifacts/multirpa-desktop-staging-2026-10-12.txt` içinde. Gerçek Windows,
imzalı yükleyici ve müşteri production kabulü bu Linux kanıtlarına dahil değildir.

Resmi sürüm/derleme sınırları:
[Electron stable releases](https://releases.electronjs.org/?channel=stable),
[safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage),
[PyInstaller platform bağı](https://pyinstaller.org/en/stable/operating-mode.html).
