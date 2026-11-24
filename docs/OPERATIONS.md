# Memasang dan menjalankan pilot KK di OCI Batam

Di Batam, API menerima upload, Object Storage menyimpan foto, dan Oracle Database menyimpan hasil per KK. Worker mengakses layanan OCI lewat instance principal. Sebelum memasang, sepakati tag aplikasi, resource tenancy, operator yang boleh masuk, dan tujuan pemrosesan model. Untuk latihan lokal dengan SQLite, pakai [panduan bulk upload](DAY-ONE.md).

## Siapkan VM dan database

Siapkan VM Linux dengan Python 3.11 dan cgroup v2, jaringan privat, Autonomous Database, bucket dengan versioning, OCI Queue, routing profile untuk model vision, serta jalur akses operator. Kalau memakai [template Terraform](../infra/README.md), jalankan `terraform init`, `terraform validate` dan `terraform plan` di lingkungan penerima. Pembuatan resource secara default dinonaktifkan. Cek plan sebelum mengaktifkannya.

Pasang aplikasi di `/opt/kk-pilot/current` dan virtual environment di `/opt/kk-pilot/venv`. Buat user layanan `kkpilot`.

DBA menyiapkan akun `KK_OWNER`, `KK_RUNTIME` dan `KK_IMPORTER`. Jalankan `sql/00_account_grants.sql` sebagai administrator. Kemudian sebagai pemilik skema, jalankan skrip di folder `sql/` sesuai urutan berikut.

1. `01_schema.sql` dan `02_runtime_grants.sql`
2. `04_cloud_schema.sql` dan `05_cloud_grants.sql`
3. `06_nik_review.sql` dan `07_nik_router.sql`

`03_operational_queries.sql` berisi query untuk operator. Oracle DDL melakukan commit implisit. Kalau instalasi berhenti di tengah, periksa dulu objek yang sudah terbentuk sebelum melanjutkan.

## Hubungkan aplikasi ke resource yang disetujui

Salin `kkpilot/cloud/config.example.json` ke `/etc/kk-pilot/cloud.json` dan `deploy/runtime.env.example` ke `/etc/kk-pilot/runtime.env`. Keduanya berada di VM.

- Isi compartment, host dan service database privat, Queue OCID dan messages endpoint, namespace, bucket, routing profile, scope pilot, serta hash gambar yang boleh diproses.
- Cocokkan `KK_OCI_COMPARTMENT_OCID` dan `KK_OCI_ROUTING_PROFILE_OCID` di environment dengan resource yang disetujui di JSON. Validator memeriksa kedua sumber konfigurasi ini.
- Simpan password database, wallet mTLS dan password wallet di tempat penyimpanan secret tim. Batasi akses ke file environment yang dibaca systemd. Tetapkan identitas operator yang akan tercatat pada keputusan review.
- Isi flag persetujuan data dan biaya, referensi izin dataset, serta nilai `ack` sesuai `kkpilot/cloud/config.py`. Konfigurasi Gemini contoh memerlukan persetujuan pemrosesan di Google Americas. Pastikan izin dari pemilik data mencakup gambar yang dikirim ke provider.

Jalankan kedua perintah ini dengan environment yang sama seperti service.

```sh
python -m kkpilot.cloud plan --config /etc/kk-pilot/cloud.json
python -m kkpilot.cloud preflight --config /etc/kk-pilot/cloud.json
```

Periksa status dan masalah yang dilaporkan untuk model, tujuan, lease, visibility timeout dan batas panggilan. Untuk memeriksa koneksi ke layanan cloud, gunakan `preflight` dengan `--execute` dari VM privat yang sudah memiliki environment dan wallet. Batasi policy IAM ke bucket, queue, model/profile serta compartment yang dipakai. Router memakai endpoint inferensi yang sama.

## Pasang OCR dan tentukan posisi crop

Pasang `kkpilot/cloud/requirements.operator.txt` di environment aplikasi. OCR memakai venv terpisah di `/opt/kk-pilot/ocr-runtime-v1/venv` dengan dependensi dari `deploy/requirements-ocr.txt`. Ambil **PP-OCRv5_mobile_det_infer** dan **PP-OCRv5_server_rec_infer** dari distribusi PaddleOCR. Tempatkan keduanya di `/opt/kk-pilot/ocr-runtime-v1/models`.

Periksa lisensi, checksum unduhan, dan dependensi native untuk VM tersebut. Catat hash manifest model yang terpasang. `local_paddle_ocr.verify_models` memeriksa hash file. `check_resource_limits` memeriksa CPU, RAM, swap dan PID. Worker OCR memakai user dan network namespaces pada kernel. Coba sebagai user layanan dengan batas cgroup yang akan dipakai.

Konfigurasi awal menjalankan jalur Gemini penuh setelah operator menyetujuinya. Untuk OCR hybrid, siapkan manifest privat yang menentukan area gambar, aktifkan `focused_refinement_enabled` dan `local_ocr.enabled`, lalu atur anggaran panggilan sesuai validator. `field_corroboration_enabled`, `nik_row_alignment_enabled` dan pembacaan tiga arah bersifat opsional dan punya prasyarat masing-masing. Jalur tiga arah memerlukan persetujuan Document Understanding serta anggaran panggilan tambahan. Cek crop pada gambar kalibrasi yang boleh dipakai. [Cara mencocokkan crop ke anggota](GEOMETRY.md)

## Nyalakan service dan cek satu job

Buat `/var/lib/kk-pilot` beserta subdirektori `tmp`, `diagnostics` dan `bulk-nik-router`. Tetapkan user layanan sebagai pemilik dan batasi akses. Router memakai data diagnostik yang dapat berisi identitas. Tentukan retensinya bersama pemilik data.

Salin `nik_router/` ke `/opt/kk-pilot/nik-router/current`. Profil crop awal berupa objek JSON kosong. Dalam kondisi ini locator memakai OCR untuk mencari anggota. Kalau lokasi baris belum jelas, NIK perlu diperiksa petugas. Pada jalur input utama, hash gambar tetap harus termasuk dalam daftar yang disetujui.

Pasang unit dari `deploy/` dan periksa `ExecStart`. Aktifkan scope di SQLcl sebagai `KK_OWNER` sebelum menyalakan timer router.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kk-pilot-api.service kk-pilot-dispatcher.service kk-pilot-worker.service
# Sebagai KK_OWNER di SQLcl, untuk scope yang sama dengan cloud.json:
# @deploy/activate-scope.sql my-approved-pilot
sudo systemctl enable --now kk-pilot-nik-router.timer
```

Buka aplikasi lewat tunnel ke port loopback 8765 pada VM. Akses cloud memakai scope, token CSRF dan identitas operator di jalur privat. Saat mencoba satu upload, ikuti job dari bukti penerimaan, gambar di bucket, worker dan commit Oracle sampai status di `/api/jobs/<id>/nik-routing`. Router hanya mengambil hasil `MODEL` dalam scope sejak waktu aktivasi di Oracle. [Langkah mengecek keputusan yang sudah tersimpan](HANDOVER.md#uji-bulk-dan-review-di-oci)

## Pantau antrean dan rollback

Cek `systemctl status kk-pilot-{api,dispatcher,worker}.service`, `systemctl status kk-pilot-nik-router.timer` dan journal masing-masing layanan. Pantau umur job tertua, waktu upload sampai commit, waktu sampai routing selesai, NIK salah yang lolos, jumlah NIK ke petugas serta panggilan dan token per job. Keputusan petugas ditambahkan sebagai event dengan request ID dan revisi yang diharapkan. Hasil awal AI tetap tersedia.

Sebelum mengganti build, tag versi lama dan baru. Simpan konfigurasi, hash dan backup secara privat. Untuk rollback router, hentikan timer, tunggu worker selesai dengan job aktif, kembalikan aplikasi atau worker ke build yang sudah diuji, lalu restart service yang berubah. Pertahankan record Oracle, gambar dan ledger. Buka hasil lama dan salah satu keputusan petugas untuk memastikan keduanya masih terbaca.

## Siapa perlu akses apa?

| Komponen | Akses yang dipakai |
|---|---|
| API dan worker (instance principal) | Mengelola pesan di queue yang ditentukan, membaca dan menulis versi object di bucket privat, membaca routing profile dan memanggil inferensi yang disetujui. |
| `KK_RUNTIME` | Membaca hasil dalam scope pilot, menambah ekstraksi, kasus dan event review sesuai grant pada skrip SQL. |
| DBA | Membuat skema dan grant, menyiapkan private endpoint, kredensial dan wallet, lalu mengaktifkan scope setelah konfigurasi diterima. |
| Router | Membaca sumber melalui API loopback dan data diagnostik. Untuk baris yang ditandai, router mencoba satu kali baca ulang jika crop dapat dipastikan. Penyimpanan receipt dan kasus menangani pengulangan tanpa membuat duplikat. |

Periksa policy bersama admin tenancy. Berikan izin Document Understanding hanya bila jalur itu aktif. VM diakses secara privat. Untuk demo web publik gunakan gambar fiktif.
