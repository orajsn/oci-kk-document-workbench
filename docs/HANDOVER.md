# Serah terima ke tim FIF

Mulai dari dua KK. Upload keduanya sekaligus, buka anggota yang perlu diperiksa, simpan satu keputusan, lalu buka lagi dari sesi baru. Di akhir tes, tim aplikasi dan DBA harus bisa menunjukkan hasil AI awal dan keputusan petugas untuk anggota yang sama.

Untuk mencoba di laptop, ambil tag yang disepakati ke folder kosong dan ikuti [latihan bulk upload](DAY-ONE.md). Data fiktif sudah ada di repo. Langkah di bawah dipakai saat tim siap menguji di tenancy sendiri.

## Sebelum menguji di OCI

| Tim | Yang perlu disiapkan |
|---|---|
| Pemilik proses | Field yang dibutuhkan pada tiap layout, aturan untuk nomor cetak lama, contoh hasil yang dapat diterima, dan keputusan kalau cetakan ambigu. Pilih dua gambar yang boleh diproses. Untuk tes review, sertakan gambar dengan NIK yang memang meragukan. |
| Platform OCI | Resource di region yang disepakati, routing profile dan tujuan inferensi yang disetujui, akses privat, wallet, dynamic group dan policy dengan hak minimum. |
| Aplikasi | Tag dan paket aplikasi serta router, versi prompt dan schema, hash model OCR, profil crop privat, tes dan build untuk rollback. |
| DBA | Skema hasil dan tabel review, grant untuk user runtime, wallet, backup/restore, dan akses operator. |
| Operasi | Health check, pemantauan antrean dan kapasitas, cara menemukan job gagal atau tertunda, serta retensi gambar dan data diagnostik. |

Di tenancy baru, ikuti [runbook instalasi](OPERATIONS.md). Ganti konfigurasi, resource yang dipin, dan wallet sesuai tenancy tujuan. Tes dulu satu upload sintetis dan catat job ID, versi `MODEL` serta hash gambar. Verifikasi rute sebelum mengirim gambar asli.

Di pilot Batam yang sudah terpasang, cocokkan versi aplikasi dan router dengan manifest paket privat. Gunakan scope uji yang disetujui dan Case ID baru agar batch mudah dibedakan dari hasil lama.

## Uji bulk dan review di OCI

1. Buka UI lewat tunnel. Cek banner **OCI Batam** dan nama model. Pilih kedua gambar, klik **Unggah berkas**, lalu catat nama file, hash sumber, Case ID dan job ID. Tiap file harus punya satu job. Job ID juga terlihat di URL API saat membuka detail.
2. Buka kedua hasil. Cocokkan data keluarga dan urutan anggota dari Oracle. Setelah panel **Pemeriksaan NIK selesai** muncul, cek berapa NIK yang ditandai, berapa yang lolos setelah baca ulang dan berapa yang masuk antrean petugas. Batch bisa selesai dengan nol kasus. Kalau itu terjadi, pakai job uji baru yang punya kasus terbuka untuk menguji penyimpanan keputusan.
3. Pada job tersebut, klik **Periksa NIK berkas ini**. Cocokkan nomor baris, nama anggota, crop dan angka hasil AI. Pilih **Konfirmasi sesuai cetakan**, **Koreksi NIK** atau **Masih tidak terbaca** sesuai gambar. Catat job dan barisnya. Tutup browser, buka lagi lewat tunnel dari sesi baru, lalu pilih **Sudah diperiksa**. Keputusan terakhir harus muncul pada anggota yang sama.
4. DBA bisa mengecek hasil sebelum dan sesudah petugas menyimpan keputusan. Dari root edisi portabel, gunakan koneksi baca ke schema yang diuji. Ganti `JOB_ID` dengan ID dari langkah pertama.

```sql
@sql/check_nik_handover.sql JOB_ID
```

Hasil query pertama memuat versi `MODEL` dan bukti bahwa router sudah memproses job. Kalau routing selesai, `FLAGGED = CLEARED + REVIEW_COUNT`. Kalau `FINISHED_AT` masih kosong, cek status job, timer dan router di runbook. Hasil query kedua memuat nomor baris, revisi dan tindakan. NIK tidak ditampilkan. Setelah keputusan pertama, cek bahwa baris itu mendapat revisi 1 dan versi `MODEL` asal tetap sama.

## Sebelum hasilnya diterima

- Buka satu kasus dari dua tab pada revisi awal yang sama. Setelah tab pertama menyimpan, aplikasi harus menolak submit dari tab kedua yang masih memakai revisi lama.
- Jalankan router sekali lagi untuk job yang sudah selesai. Cocokkan jumlah request dan kasus dengan catatan sebelumnya. Kalau hasil satu request belum diketahui, cek ledger sebelum mengambil tindakan.
- Simpan tag aplikasi dan router, scope, sumber serta tujuan pemrosesan yang disetujui, kedua job ID, dan hasil cek sesi baru di lokasi internal. Catat tiga waktu terpisah, yaitu klik upload, commit Oracle terakhir dan routing terakhir.
- Sebelum menambah beban, uji pada sampel terpisah dengan label independen. Gunakan pola kedatangan saat jam sibuk dan target p95 yang disepakati. Perubahan ambang baca ulang perlu hasil uji tersebut dan persetujuan rilis sendiri.
