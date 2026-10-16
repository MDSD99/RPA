# DATABASE ARCHITECTURE

Bu RPA production ortamında merkezi bir database kullanacaktır.

Tercih edilen database:

PostgreSQL

Database, RPA uygulamasının çalıştığı makinenin içine gömülü geçici bir database olarak tasarlanmayacaktır.

Production ortamında bizim sağlayacağımız cloud/server altyapısında çalışacaktır.

Önerilen mimari:

RPA Worker / Application
↓
Database Service Layer
↓
PostgreSQL
↓
Private Cloud / Private Network

Database bağlantısı mümkün olduğunca public internet üzerinden yapılmamalıdır.

RPA server ile PostgreSQL server:

* aynı private network,
* aynı VPC,
* VPN,
* private subnet

gibi güvenli bir ağ üzerinden haberleşmelidir.

Database host, port, database name, username ve password source code içerisine hard-code edilmemelidir.

Örneğin:

DB_HOST=
DB_PORT=5432
DB_NAME=
DB_USER=
DB_PASSWORD=

değerleri environment variable veya secret-management sistemi üzerinden alınmalıdır.

Connection string loglara kesinlikle yazılmamalıdır.

---

## DATABASE RESPONSIBILITIES

Database yalnızca veri saklamak için değil, RPA execution state yönetimi için de kullanılmalıdır.

Aşağıdaki verilerin database üzerinden tutulmasını değerlendir:

### rpa_jobs

Her RPA çalıştırmasını temsil eder.

Örnek alanlar:

id
job_id
workflow_name
status
started_at
finished_at
total_records
success_count
failed_count
skipped_count
created_at
updated_at

Status örnekleri:

PENDING
RUNNING
COMPLETED
PARTIALLY_COMPLETED
FAILED
CANCELLED

---

### rpa_transactions

İşlenen her business kaydını temsil eder.

Örnek:

id
job_id
record_id
external_reference
status
attempt_count
started_at
finished_at
last_error
result_data
created_at
updated_at

Status:

PENDING
PROCESSING
SUCCESS
FAILED
SKIPPED
RETRY

record_id veya uygun business key için gerekli UNIQUE constraint'leri değerlendir.

Duplicate işlem riskini database seviyesinde de engelle.

---

### rpa_errors

Detaylı hata geçmişi.

Örnek:

id
job_id
transaction_id
error_type
error_message
step
stack_trace
screenshot_path
created_at

Hassas verilerin error kayıtlarına yazılmamasına dikkat et.

---

### rpa_checkpoints

Uzun süren workflow'larda resume/recovery için kullanılabilir.

Örnek:

id
job_id
workflow
checkpoint_key
checkpoint_value
created_at
updated_at

---

### rpa_audit_log

Business açısından önemli aksiyonların gerektiğinde audit trail olarak saklanmasını değerlendir.

Örnek:

id
job_id
record_id
action
old_value
new_value
created_at

Ancak gereksiz derecede büyük veya hassas payload'ları database'e kaydetme.

---

# DATABASE ACCESS LAYER

Business logic içerisinde doğrudan SQL sorgularını dağıtma.

Database erişimini ayrı katmanda yönet.

Örnek:

app/
├── database/
│   ├── connection.py
│   ├── models.py
│   ├── repositories/
│   │   ├── job_repository.py
│   │   ├── transaction_repository.py
│   │   └── error_repository.py
│   └── migrations/
│
├── services/
├── automation/
└── core/

Repository/service pattern kullan.

Örneğin:

transaction_repository.mark_processing(record_id)

transaction_repository.mark_success(
record_id,
result
)

transaction_repository.mark_failed(
record_id,
error
)

Business logic'in PostgreSQL detaylarına doğrudan bağımlı olmamasını sağla.

---

# ORM / DATABASE LIBRARY

Python projesinde ihtiyaca göre:

SQLAlchemy

kullanmayı değerlendir.

Database schema değişiklikleri için migration sistemi kullan.

Tercihen:

Alembic

Database tablolarını application startup sırasında rastgele CREATE TABLE sorgularıyla değiştirme.

Schema versiyonlarını migration ile yönet.

---

# CONNECTION MANAGEMENT

Database connection pooling kullan.

Her kayıt için yeni database connection açıp kapatma.

Connection kaybolması gibi geçici hatalarda kontrollü retry uygula.

Ancak transaction'ın gerçekten commit edilip edilmediği belirsiz durumlarda aynı business işlemini körü körüne tekrar çalıştırma.

Idempotency mekanizmasını kullan.

---

# DATABASE TRANSACTIONS

Kritik state değişikliklerinde database transaction kullan.

Örneğin bir kayıt işlemeye başlarken:

PENDING
↓
PROCESSING

İşlem başarılıysa:

PROCESSING
↓
SUCCESS

İşlem başarısızsa:

PROCESSING
↓
FAILED

State geçişlerini mümkün olduğunca atomik yap.

---

# CONCURRENCY

Birden fazla RPA worker aynı anda çalışabilecek şekilde tasarımı değerlendir.

Aynı kaydın iki farklı worker tarafından aynı anda işlenmesini engelle.

Bunun için gerektiğinde:

* database locking,
* SELECT FOR UPDATE,
* atomic status update,
* unique constraints,
* job claiming

gibi mekanizmaları kullan.

Örneğin worker bir kayıt aldığında transaction'ı atomik olarak:

PENDING → PROCESSING

durumuna geçirmelidir.

Başka worker aynı kaydı alamamalıdır.

---

# CRASH RECOVERY

Worker çökerse PROCESSING durumunda kalan kayıtların sonsuza kadar takılı kalmamasını sağla.

Örneğin:

processing_started_at

alanı tut.

Belirli süreden uzun süredir PROCESSING durumunda kalan kayıtları stale olarak değerlendir.

Güvenliyse yeniden PENDING/RETRY durumuna geçir.

Ancak business sisteminde işlemin gerçekleşmiş olma ihtimali varsa önce reconciliation yap.

---

# IDEMPOTENCY

Production RPA'da duplicate işlem kritik risk olarak değerlendirilsin.

Her business transaction için mümkün olduğunda idempotency key oluştur.

Örneğin:

source_system + record_id + operation_type

aynı işlem daha önce SUCCESS olduysa tekrar gerçekleştirilmemeli.

Sadece database'deki status'a güvenmenin yeterli olmadığı kritik sistemlerde hedef sistemden de sonucu doğrula.

---

# DEVELOPMENT VS PRODUCTION

Development ortamında local PostgreSQL veya gerekli görülürse SQLite kullanılabilir.

Ancak SQLite'a özgü kod yazma.

Production ana database:

PostgreSQL

olarak tasarlansın.

Configuration üzerinden environment seçilebilsin:

APP_ENV=development
APP_ENV=test
APP_ENV=production

---

# DOCKER

Altyapıya uygunsa PostgreSQL'in Docker üzerinden çalıştırılmasını değerlendir.

Development için gerekirse:

docker-compose.yml

oluştur.

Örneğin servisler:

postgres
rpa-worker

Ancak production deployment mimarisini development docker-compose dosyasına bağımlı hale getirme.

---

# DATABASE BACKUP

Production database için backup stratejisini README içerisinde belirt.

En azından:

* otomatik backup,
* backup retention,
* restore prosedürü

konularına değin.

RPA uygulaması backup'ın kendisini yönetmek zorunda değildir; ancak altyapı gereksinimi olarak dokümante edilmelidir.

---

# DATABASE SECURITY

Production database için:

* public access kapalı olmalı,
* minimum yetkili DB user kullanılmalı,
* root/superuser hesabı RPA tarafından kullanılmamalı,
* mümkünse encrypted connection kullanılmalı,
* credential source code'a yazılmamalı,
* DB password loglanmamalı,
* network erişimi yalnızca gerekli RPA server'larına verilmelidir.

---

# FINAL DATABASE DELIVERABLE

Implementation sonunda ayrıca şunları göster:

1. Database schema
2. Entity ilişkileri
3. Migration dosyaları
4. Database config
5. Gerekli environment variables
6. Local development database kurulumu
7. Production database bağlantı yöntemi
8. Backup/recovery önerisi
9. Concurrency yaklaşımı
10. Idempotency yaklaşımı
11. Crash recovery yaklaşımı

Database'i yalnızca CRUD storage olarak düşünme.

Database aynı zamanda RPA'nın:

* state management,
* checkpoint,
* transaction tracking,
* retry management,
* duplicate prevention,
* recovery

mekanizmasının merkezi parçası olarak tasarlanmalıdır.
