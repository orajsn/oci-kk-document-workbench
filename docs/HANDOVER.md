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

## Uji bulk dan review di OCI

**Lingkungan baru.** Platform engineer menyiapkan resource Batam, profile inferensi yang sudah disetujui, hak instance principal, dan tunnel privat sesuai [runbook](OPERATIONS.md). DBA menyiapkan schema, wallet dan user runtime. Pemilik proses menyiapkan dua gambar berizin beserta hash sumber dan tujuan pemrosesannya. Di deployment Batam yang sudah ada, cocokkan rilis yang sedang aktif dengan manifest dalam paket privat. Jalankan pengujian pada scope uji yang disetujui. Gunakan Case ID baru supaya hasil demo lama bisa dibedakan.

1. Di UI lewat tunnel, periksa banner **OCI Batam**. Pastikan mode menunjukkan model yang disetujui. Pilih dua gambar sekaligus, klik **Unggah berkas**, catat nama file, Case ID dan job ID masing-masing. Job ID juga ada pada URL API ketika membuka detail.
2. Pada setiap detail, tunggu hasil keluarga dan anggota tersimpan. Tunggu panel **Pemeriksaan NIK selesai**. Cocokkan jumlah anggota, ter-flag, lolos setelah baca ulang dan ke petugas. Nol kasus review merupakan hasil yang valid. Untuk menguji penyimpanan keputusan, gunakan job uji baru yang benar-benar menghasilkan setidaknya satu kasus. Pilih sampel berizin dengan kasus ambigu yang dikenal sebelum demo.
3. Klik **Periksa NIK berkas ini** pada job dengan kasus terbuka. Cocokkan baris, crop, konteks dan nilai AI. Pilih **Konfirmasi sesuai cetakan**, **Koreksi NIK**, atau **Masih tidak terbaca** sesuai sumber. Setelah simpan, catat job, nomor baris dan keputusan. Tutup browser, buka melalui tunnel dari sesi baru dan gunakan filter **Sudah diperiksa**. Pastikan kasus yang sama menunjukkan keputusan terakhir.
4. Dengan koneksi baca ke schema tersebut, DBA menjalankan perintah berikut di SQLcl dari root edisi portabel (ganti `JOB_ID` dengan ID job yang baru). Jalankan sebelum dan sesudah menyimpan keputusan untuk membandingkan versi MODEL dan riwayat event:

```sql
@sql/check_nik_handover.sql JOB_ID
```

Output pertama menampilkan versi MODEL dan receipt routing. Jika routing telah selesai, `FLAGGED = CLEARED + REVIEW_COUNT`. Output kedua menampilkan baris anggota, revisi dan tindakan tanpa mencetak NIK. Setelah keputusan pertama, revisi 1 muncul untuk baris tersebut dan versi MODEL asal tetap sama. Jika `FINISHED_AT` kosong, periksa timer/router dan status job pada runbook. Nilai kosong ini berbeda dari hasil selesai dengan nol kasus.

5. Catat tag rilis, scope, sumber yang disetujui, kedua job ID, waktu upload hingga commit/routing, serta hasil pembukaan ulang. Simpan receipt di lokasi internal. Promosi ambang baru memerlukan hasil holdout dan persetujuan rilis tersendiri.
