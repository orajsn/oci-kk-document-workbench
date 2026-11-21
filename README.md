# KK Document Workbench for OCI

Ekstraksi Kartu Keluarga dari gambar menjadi record rumah tangga dan baris anggota. Aplikasi menyediakan bulk upload, penyimpanan sumber, antrean ekstraksi dan pemeriksaan NIK per anggota. Ketika satu angka sulit dibaca, petugas dapat membuka crop, membandingkannya dengan nilai AI, lalu mencatat keputusan secara terpisah.

## Coba di laptop

Gunakan Python 3.11+. Mode demo memakai SQLite dan tidak memerlukan credential OCI.

```sh
python run_local.py --port 8765 --data-dir runtime
```

1. Buka `http://127.0.0.1:8765`, pilih **Upload KK Images**, isi case ID dan pilih **Synthetic Demo**.
2. Unggah [gambar contoh](demo/synthetic-kk.png). Buka hasil keluarga dan anggota, lakukan review, lalu buka ulang record. Demo memakai keluaran fiktif tetap untuk menguji alur.
3. Untuk mencoba persis interaksi review sebuah field tanpa database cloud, buka [simulasi HTML](demo/field-review.html) dari clone lokal. Simulator ini menggunakan gambar buatan sendiri dan menyimpan keputusan di browser.
4. Coba perubahan ambang OCR pada [lab tuning](examples/tuning_lab.py). Perintah ini membandingkan dua kebijakan pada data sintetis.

```sh
python examples/tuning_lab.py --compare 0.80 0.90
python examples/score_documents.py --reference examples/synthetic-reference.json --predictions examples/synthetic-prediction.json
python -m unittest discover -s tests -p "test_backend_*.py"
```

Untuk suite lengkap termasuk fixture citra, siapkan virtual environment dan instal `requirements-test.txt`, lalu jalankan `python -m unittest discover -s tests`. Test preprocessing berbasis OpenCV akan dilewati bila library opsionalnya tidak ada.

## Arsitektur deployment OCI

```mermaid
flowchart LR
    U[Petugas] --> A[API pada OCI Compute]
    A --> O[OCI Object Storage: gambar asli]
    A --> D[(Oracle Autonomous Database)]
    A --> Q[OCI Queue]
    Q --> W[Worker: OCR dan ekstraksi]
    O --> W
    W --> G[OCI Generative AI routing profile]
    G --> M[Gemini vision]
    W --> D
    D --> R[Worker review NIK]
    R -->|Crop yang perlu dibaca ulang| G
    R --> D
    D --> UI[UI: crop, nilai asli dan keputusan]
```

Entry point contoh memakai OCI Batam. Rute on-demand contoh menargetkan Chicago dengan pemrosesan Gemini di Google Americas. Pemilik deployment memasok, memverifikasi dan menyetujui profile sendiri. API cloud bind ke loopback dan dirancang untuk diakses melalui tunnel privat. Identitas operator di pilot mengikuti identitas SSH terkonfigurasi; akses multi-petugas produksi membutuhkan autentikasi per pengguna.

## Titik tuning

| Saat ada masalah | Yang diubah | Yang dibuktikan |
|---|---|---|
| Angka dari anggota lain ikut terbaca | Orientasi, batas crop dan pengikatan nama ke baris | Crop lengkap untuk orang yang benar |
| Nilai di gambar terlihat tetapi hasil berubah format atau field | Prompt, kontrak JSON dan parser | Karakter sumber, leading zero, null dan urutan anggota tetap terjaga |
| Terlalu banyak atau terlalu sedikit review | Ambang bukti OCR, konflik struktur, baca ulang dan aturan nomor lama | NIK salah lolos, NIK benar ikut review, waktu dan panggilan per dokumen |

Panduan [tuning dan evaluasi](docs/TUNING.md) menjelaskan file serta fungsi yang mengendalikan tiap tahap. Model Gemini dipanggil lewat layanan terkelola. Perubahan di sini memengaruhi input, instruksi, validasi dan routing. Bobot model tidak dilatih oleh aplikasi.

## Untuk tim penerima

- [Runbook instalasi, layanan dan rollback](docs/OPERATIONS.md)
- [Checklist serah terima dan uji penerimaan](docs/HANDOVER.md)
- [Riwayat perubahan](CHANGELOG.md)

| Direktori | Isi |
|---|---|
| `kkpilot/`, `web/` | API, UI, worker, kontrak model, pipeline OCR dan penyimpanan |
| `nik_router/` | Pemeriksa post-commit, pembacaan crop, ledger dan kebijakan format lama |
| `sql/` | Skema Oracle, grant runtime dan event keputusan review |
| `deploy/`, `infra/` | Unit systemd, contoh environment dan Terraform yang dinonaktifkan secara default |
| `tests/`, `examples/`, `demo/` | Pemeriksaan lokal dan contoh sintetis |

Repo publik memakai konfigurasi kosong untuk resource tenancy dan profil crop. Buat konfigurasi privat milik penerima sebelum live. Foto keluarga asli, OCR response, evaluator berisi data pribadi, wallet, state Terraform dan deck customer disimpan di luar repo.
