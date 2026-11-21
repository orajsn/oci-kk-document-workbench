# Jalur instalasi OCI

Kode cloud dipisah dari jalur SQLite. Contoh konfigurasi menggunakan Batam untuk storage dan aplikasi, instance principal untuk layanan OCI, dan tujuan model yang dibatasi oleh adapter. Untuk pemetaan deployment gunakan versi tagged dari repo ini dan file konfigurasi yang dibuat tim penerima.

## 1. Resource dan database

Siapkan VM Linux (Python 3.11, cgroup v2), jaringan privat, Autonomous Database, bucket dengan versioning, OCI Queue, routing profile untuk model vision, dan jalur akses operator. `infra/` berisi rancangan Terraform yang perlu di-review serta diuji dengan `terraform init`, `validate` dan `plan` pada lingkungan penerima. Default deployment nonaktif.

Pasang aplikasi di `/opt/kk-pilot/current` dan virtual environment di `/opt/kk-pilot/venv`. Buat akun layanan `kkpilot`. DBA menyediakan akun KK_OWNER, KK_RUNTIME, KK_IMPORTER, kemudian menjalankan `sql/00_account_grants.sql` sebagai administrator. Jalankan `01_schema.sql`, `02_runtime_grants.sql`, `04_cloud_schema.sql`, `05_cloud_grants.sql`, `06_nik_review.sql` dan `07_nik_router.sql` secara berurutan sebagai pemilik skema. `03_operational_queries.sql` berisi pemeriksaan operator. Oracle DDL melakukan commit implisit; jika ada kegagalan, cocokkan objek yang sudah terbentuk sebelum melanjutkan.

## 2. Konfigurasi milik penerima

Salin `kkpilot/cloud/config.example.json` ke `/etc/kk-pilot/cloud.json` dan `deploy/runtime.env.example` ke `/etc/kk-pilot/runtime.env` di VM. Isi compartment, private DB host/service, Queue OCID/messages endpoint, namespace, bucket, routing profile, scope dan hash gambar yang disetujui. Isi kedua pin independen `KK_OCI_COMPARTMENT_OCID` dan `KK_OCI_ROUTING_PROFILE_OCID` agar setara dengan resource yang disetujui di JSON. Simpan password DB, wallet mTLS, dan password wallet di kanal secret tim; file environment dibaca systemd secara privat. Tetapkan identitas operator untuk event review.

Approval konfigurasi terdiri dari flag data/spend, referensi otorisasi untuk dataset, serta `ack` sesuai konstanta pada `kkpilot/cloud/config.py`. Setelan model memerlukan approval pemrosesan Google Americas. Pemilihan model, tujuan, lease, visibility timeout, dan batas panggilan diverifikasi sebelum mulai. Preview konfigurasi:

```sh
python -m kkpilot.cloud plan --config /etc/kk-pilot/cloud.json
python -m kkpilot.cloud preflight --config /etc/kk-pilot/cloud.json
```

Jalankan perintah tersebut dengan environment pin yang sama dengan layanan. Periksa status dan daftar masalah yang dikembalikan. `--execute` dengan `preflight` melakukan probe cloud. Lakukan preflight dari VM privat yang sudah punya environment dan wallet. Uji browser lokal tidak memerlukan resource tersebut. Buat policy IAM yang dibatasi ke bucket, queue, model/profile dan compartment penerima. Router memakai endpoint inferensi yang sama. Hubungi pemilik data untuk izin gambar yang dapat dikirim ke provider.

## 3. OCR, geometri, dan layanan

Instal `kkpilot/cloud/requirements.operator.txt` pada environment aplikasi. Untuk jalur OCR gunakan venv terpisah di `/opt/kk-pilot/ocr-runtime-v1/venv` dengan `deploy/requirements-ocr.txt`. Ambil model **PP-OCRv5_mobile_det_infer** dan **PP-OCRv5_server_rec_infer** dari distribusi PaddleOCR dan tempatkan pada `/opt/kk-pilot/ocr-runtime-v1/models`. Verifikasi lisensi, checksum unduhan dan dependensi native platform. Tetapkan hash manifest untuk model yang benar-benar terpasang. `local_paddle_ocr.verify_models` memeriksa pin file dan `check_resource_limits` memeriksa CPU, RAM, swap dan PID. Worker OCR membutuhkan user/network namespaces pada kernel; lakukan smoke test sebagai user layanan dengan batas cgroup.

Konfigurasi awal mengaktifkan jalur Gemini penuh setelah approval operator. Untuk menyalakan OCR hybrid, siapkan manifest focused source privat terlebih dahulu, aktifkan `focused_refinement_enabled`, `local_ocr.enabled` dan anggaran panggilan sesuai validator. `field_corroboration_enabled`, `nik_row_alignment_enabled` dan pembacaan tiga arah merupakan tahap opsional dengan prasyarat tersendiri. Opsi tiga arah juga membutuhkan persetujuan Document Understanding dan anggaran panggilan tambahan. Gunakan gambar kalibrasi milik penerima untuk memastikan crop benar. Lihat [kontrak geometri](GEOMETRY.md).

Buat `/var/lib/kk-pilot` dan subdirektori `tmp`, `diagnostics`, serta `bulk-nik-router` dengan pemilik layanan dan izin privat. Data diagnostic diperlukan oleh router, dapat berisi informasi identitas dan perlu kebijakan retensi. Salin `nik_router/` ke `/opt/kk-pilot/nik-router/current`. Profil crop awal adalah objek JSON kosong; locator mencoba pengikatan dari OCR, dan kasus dengan lokasi yang belum jelas memerlukan review. Jalur input utama tetap menyaratkan hash sumber yang disetujui.

Pasang unit dari `deploy/`, periksa path `ExecStart`, lalu jalankan:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kk-pilot-api.service kk-pilot-dispatcher.service kk-pilot-worker.service
# Sebagai KK_OWNER di SQLcl, untuk scope yang sama dengan cloud.json:
# @deploy/activate-scope.sql my-approved-pilot
sudo systemctl enable --now kk-pilot-nik-router.timer
```

Akses dashboard melalui tunnel ke loopback port 8765 pada VM. Akses cloud memakai kontrol scope, token CSRF dan identitas operator melalui jalur privat. Uji pembuatan job, receipt, Object Storage, worker, commit Oracle, antrean review dan pembukaan ulang keputusan. Endpoint `/api/jobs/<id>/nik-routing` menyajikan status per job. Worker post-commit hanya memilih hasil MODEL dalam scope sejak waktu aktivasi di Oracle.

## 4. Operasi dan rollback

Lihat `systemctl status kk-pilot-{api,dispatcher,worker}.service`, `systemctl status kk-pilot-nik-router.timer` dan journal layanan. Pantau usia job tertua, waktu upload-ke-commit dan routing, NIK salah lolos, review burden, serta panggilan/token per job. Koreksi operator ditambahkan sebagai event dengan expected revision dan request ID. Nilai AI awal tetap tersedia.

Tag build lama dan baru; simpan konfigurasi, hash dan backup di penyimpanan privat. Untuk rollback router, hentikan timer, tunggu worker idle lalu kembalikan target aplikasi/worker ke build tervalidasi dan mulai ulang layanan yang berubah. Pertahankan data Oracle, blob gambar dan ledger receipt. Uji bahwa record dan event lama tetap dapat dibuka.

### Tanggung jawab akses

| Komponen | Operasi yang digunakan |
|---|---|
| API dan worker melalui instance principal | Kelola pesan pada queue yang ditetapkan, tulis/baca versi object pada bucket privat, baca model routing profile dan panggil inference yang diizinkan |
| Database KK_RUNTIME | Baca hasil dalam scope pilot, tambah ekstraksi, kasus serta event review sesuai grant pada skrip SQL |
| DBA | Buat schema dan grant, siapkan private endpoint, kredensial dan wallet; aktifkan scope post-commit setelah konfigurasi diterima |
| Router | Baca sumber melalui API loopback dan diagnostic bukti, lakukan satu percobaan reread per baris ter-flag bila crop aman, tulis receipt dan kasus baru secara idempoten |

Periksa policy IAM dengan administrator tenancy, termasuk izin Document Understanding hanya jika jalur itu diaktifkan. Jangan memberi akses publik ke VM atau menjadikan foto contoh sungguhan sebagai asset web.
