# MultiRPA akış ve düğüm sürümü sözleşmesi

Tarih: 13 Ekim 2026. Durum: düğüm davranış sürümü uygulaması ve doğrulaması
sürüyor; tamamlanan koşumlar [VALIDATION.md](../VALIDATION.md) içinde izlenir.
Döngü, alt akış, hata dalları ve uzun iş desteği bu teslimde eklenmez.

Bu sözleşme web ve masaüstünün aynı akış tanımını saklayabilmesini ve
çalıştırılan davranışın gelecekteki katalog değişiklikleriyle sessizce
değişmemesini amaçlar. Kullanıcı işlemleri [Flow Studio rehberindedir](FLOW_STUDIO.md).

## Birbirinden ayrı üç sürüm

| Alan | Anlam | Değişme nedeni |
| --- | --- | --- |
| Akış kaydındaki `version` | Belirli akış UUID'sinin kayıt revizyonu | Kullanıcı akışı kaydeder; çalışma mevcut sabit revizyona bağlanır |
| Düğümdeki `type_version` | Belirli `type` için davranış sözleşmesi | Geliştirici aynı türün uyumsuz yeni davranışını ekler |
| Arşivdeki `schema_version` | `.multirpa.json` taşıma zarfının biçimi | Eski okuyucunun anlayamadığı bir temsil eklenir |

Akış revizyonu 7 olan bir kayıtta v1 düğümler bulunabilir ve bu akış arşiv v2
ile taşınabilir. İçe aktarma hedefte yeni UUID ve akış revizyonu 1 üretir;
düğüm davranışlarını bu nedenle yükseltmez. Arşiv numarası ürün sürümü değildir.

## Düğüm kimliği ve eski akışların korunması

- Çalıştırılabilir düğüm `(type, type_version)` çiftiyle çözülür. İlk sürümlü
  katalog mevcut 20 türün yalnız davranış sürümü **1** kaydını içerir.
- `type_version` alanının bulunmaması kalıcı olarak **v1** demektir. Gelecekte
  v2 eklenmesi, alanı bulunmayan eski akışı yeni davranışa geçirmez.
- Açık sürüm JSON tam sayısı `1` olmalıdır. `true`, `1.0`, `"1"`, `null`,
  sıfır, negatif veya desteklenmeyen sürüm kabul edilmez.
- Doğrulama eksik sürüm alanını grafiğe eklemez. Eski düğümlerin JSON biçimi,
  grafik özeti, sabit çalışma girdisi ve checkpoint uyumluluğu korunur.
- Yeni editör düğümleri ve şablonlar açık `type_version: 1` yazar. Eski akış
  yalnız açıldığı, görüntülendiği veya dışa aktarıldığı için yeniden yazılmaz.
  Eski akışa yeni düğüm eklemek, alanlı ve alansız düğümlerin birlikte bulunduğu
  geçerli bir grafik oluşturabilir.
- Katalog kaydı ve davranış sürümlerinin listesi değişmez değerlerdir. Bir
  API yanıtını veya editör metadata'sını değiştirmek yürütme otoritesini
  değiştirmez. Flow girdisi keyfi kod yükleme ya da `eval` mekanizması değildir.

Örnek temsiller aynı v1 başlangıç davranışını seçer; kaydedilmiş temsilleri farklıdır:

```json
{"id": "start", "type": "start", "config": {}}
```

```json
{"id": "start", "type": "start", "type_version": 1, "config": {}}
```

## Arşiv uyumluluğu

| `schema_version` | İçe aktarma | Dışa aktarmada seçim |
| --- | --- | --- |
| `1` | Yalnız `type_version` alanı bulunmayan eski düğümler | Grafikte hiçbir düğüm açık sürüm taşımıyorsa |
| `2` | Desteklenen açık sürümler, eski düğümler veya ikisi birlikte | En az bir düğüm açık sürüm taşıyorsa |

Dışa aktarma zarf sürümünü otomatik seçer; kaynak grafiğe sürüm eklemez,
sürüm silmez ve kayıt revizyonunu değiştirmez. Arşiv v1 içine açık sürümlü
düğüm koymak reddedilir. Böylece eski v1 okuyucunun anlayamadığı düğüm
alanları sessizce v1 belgesi olarak dağıtılmaz. Eski masaüstü uygulamasına v2
belgesi aktarmak için alanları elle silmek yerine uygulama güncellenir.

Zarf yine yalnız `format`, `schema_version`, `flow`; `flow` yalnız `name`,
`description`, `graph` taşır. Bilinmeyen sürümler/alanlar reddedilir. 128 KiB
dosya ve mevcut grafik sınırları korunur. Sır kasası değerleri, hesap/cihaz
kimliği, lisans, checkpoint veya çalıştırma geçmişi eklenmez. Akışa yazılmış
sabit müşteri metni dosyada kalabilir.

## Şema, yürütme yeteneği ve platform kabulü

Üç farklı kontrol aynı anlamda kullanılmaz:

1. **Şema desteği:** katalog türü/sürümü tanır; yapılandırma ve grafik
   kuralları geçerlidir. Dosyayı açmak ve düzenlemek için gereken kontroldür.
2. **Yürütme yeteneği:** seçilen runner ilgili platform/adaptör yeteneğine
   sahiptir ve gerekli yapılandırma kullanılabilirdir. Motor tüm grafiği ilk
   dış etkiden önce denetler; desteklenmeyen sürüm veya yetenekle kısmi
   çalıştırma başlatılmaz.
3. **Gerçek platform kabulü:** ilgili paket, işletim sistemi ve müşteri hedefi
   üzerinde davranış sınanmıştır. Katalogda Windows türünün bulunması veya
   Linux'ta unit test geçmesi gerçek Windows UIA/DPAPI kabulünü kanıtlamaz.

Şema bakımından geçerli Windows adımlarının Linux'ta düzenlenebilmesi,
Linux runner'ın onları çalıştırabileceği anlamına gelmez. Yetenek bildirimi
lisans, tenant, kasa ve hedef izin kontrollerinin yerine geçmez; bu denetimler
çalıştırma yolunda ayrıca uygulanır.

## Bu aşamanın kabulü

- Eski alansız grafikler aynı temsille doğrulanır; özeti ve checkpoint'ten
  devam davranışı değişmez.
- Açık v1 ile aynı davranış seçilir; geçersiz sürüm türleri ve desteklenmeyen
  sürümler web API, yerel runner ve yürütme ön denetiminde reddedilir.
- Arşiv v1/v2, karma grafik ve yeni UUID/kota kuralları iki ortamda doğrulanır.
- Editör yeni düğümlere açık sürüm ekler; eski akışı açmak tek başına grafiği
  dönüştürmez. İçe/dışa aktarma dosya köprüsü iki zarf sürümünü korur.
- Tenant, sabit flow revizyonu, claim/lease/fencing, retry, lisans bitişi,
  sırların sızmaması ve belirsiz dış sonuç davranışı regresyonda korunur.
- Tam koşum ve paket doğrulaması, ilgili kaynak sürümü için ayrı kayıt taşır.

Mevcut sınırlar **DAG, 2–40 düğüm, 78 bağlantı, 16 KiB grafik ve toplam
120 saniye** olarak kalır. Düğüm sürümlemesi tek başına döngü ya da uzun iş
desteği oluşturmaz; düğüm başına davranış süresi mevcut adaptör kurallarına
tabidir.

## Sonraki aşama: kontrollü döngü ve alt akış tasarımı

Aşağıdaki maddeler tasarım/kabul işidir; henüz uygulanmış özellik değildir.

1. **Sınırlı tekrar:** ilk `foreach` açık bir koleksiyon üzerinde çalışmalı;
   iterasyon sayısı, toplam yürütülen adım ve iç içe döngü derinliği ayrı
   bütçelere bağlanmalı. Sınırsız geri kenar veya belirsiz `while` eklenmemeli.
2. **Ortak zaman bütçesi:** her tur veya alt akış süreyi sıfırlamamalı. Tüm
   çalışma tek genel deadline, güncel lisans ve iptal bağlamı taşımalı. Uzun iş
   tasarımı kalıcı bekleme ve yeniden sahiplenmeyi ayrıca ele almalı.
3. **Tekil checkpoint adresi:** adım adresi yalnız node ID olmamalı; alt akış
   çağrı yolu, döngü kimliği ve iterasyon konumu içermeli. Aynı düğümün farklı
   turlarının niyet/sonuç kayıtları birbirini ezmemeli. Recovery ilerlemeyi
   kalan bütçelerle birlikte geri yüklemeli.
4. **Sabit alt akış bağı:** alt akış tenant + akış UUID + sabit kayıt revizyonu
   ile çözülmeli. Çalışma sırasında “en son sürüm” seçilmemeli; doğrudan ve
   dolaylı çağrı döngüleri sınırlandırılmalı, giriş/çıkış sözleşmesi açık olmalı.
5. **Tüm kimliklerde kota ve yetki:** çağrı ağındaki bütün akış UUID'leri aynı
   tenant/yetki/kota denetiminden geçmeli. Alt akış içine iş saklayarak ortak
   üç akış sınırı aşılmamalı. Masaüstünde gerekli opak kimlik izinleri alınmalı;
   iş içeriğinin cihazda kalması ilkesi korunmalı.
6. **Tarayıcı kurtarma:** dış etkili browser adımının belirsiz sonucu yeni
   iterasyon sayılarak yeniden gönderilmemeli. Oturum kurtarma ve reconciliation
   kuralları iterasyon/çağrı adresiyle eşleşmeli; doğrulanamayan işlem yetkili
   çözüm beklemeli.
7. **Hata kapsamı:** iş hatası, güvenli retry ve belirsiz dış sonuç ayrılmalı.
   Hata dalı veya `catch` yetki kaybını, lisans bitişini ve belirsiz sonucu
   başarı gibi yutarak yeni dış etki başlatmamalı.
8. **Ortak web/masaüstü kabulü:** iç içe döngü, kısmi turda kesinti, lease
   kaybı, restart, alt akış sürümü değişmesi, çapraz tenant, kota ve demo bitişi
   senaryoları aynı motor sözleşmesiyle sınanmalı.
