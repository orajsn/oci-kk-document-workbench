# Serah terima ke tim penerima

| Pemilik | Bukti yang harus tersedia saat menerima |
|---|---|
| Pemilik proses | Definisi field per layout, kebijakan nomor lama, penanganan dokumen ambigu, dan contoh hasil yang diterima |
| Platform OCI | Resource dan rute inference pada region yang disepakati, akses privat, pengelolaan wallet, grup dinamis dan hak minimum |
| Tim aplikasi | Versi Git dan paket, hash model OCR, versi prompt/schema, profil crop privat, tes dan prosedur rollback |
| DBA | Skema sumber dan tabel review terpisah, grant, backup/restore dan pemeriksaan akses operator |
| Operasi | Health check, monitor antrean, kasus gagal/tertunda, kapasitas, retensi gambar dan diagnostic records |

## Uji penerimaan berurutan

1. Clone tag yang disepakati ke direktori kosong. Jalankan UI lokal dan lab sintetis dari README.
2. Gunakan tenancy uji milik penerima. Persiapkan konfigurasi operator berdasarkan runbook. Tes satu unggahan sintetis yang diizinkan dan catat ID job, versi ekstraksi MODEL, serta hash sumber.
3. Uji satu batch berisi gambar yang memiliki izin pemrosesan. Pastikan jumlah job sesuai jumlah file. Verifikasi data keluarga dan anggota kembali dari Oracle. Catat timestamp klik, commit terakhir dan routing terakhir.
4. Pada kasus yang butuh review, buka crop, simpan koreksi dan buka lagi pada sesi browser baru. Bandingkan snapshot AI sebelum/sesudah. Uji dua tab dari revisi sama: submit kedua harus ditolak saat versi berubah.
5. Ulang satu siklus router atas job yang telah selesai. Cocokkan jumlah request dan kasus. Untuk request yang tidak diketahui hasilnya, periksa ledger sebelum tindakan manual.
6. Sebelum memperbesar beban, gunakan sampel holdout dengan label independen, kurva kedatangan jam sibuk dan target p95. Simpan keputusan penerimaan beserta data uji pada lokasi privat.

Catat versi paket aplikasi dan router secara terpisah. Untuk perpindahan tenant, ganti konfigurasi, resource pins dan wallet serta validasi rute sebelum memakai data real. Fixture atau hasil customer tidak diperlukan untuk clone dan uji lokal.
