# Riwayat rilis

## 0.4.2 · 2026-11-24

Saat upload beberapa KK, petugas bisa melihat berapa berkas yang sudah diterima, gagal dikirim, atau masih perlu dicek status pengirimannya. Setelah data KK tersimpan, halaman hasil menunjukkan jumlah NIK yang masuk antrean petugas.

Di layar pemeriksaan, alasan kasus muncul di atas gambar beserta bagian yang perlu dicocokkan. Misalnya, bila sistem mencatat perbedaan pada digit kelima dari kiri, layar menyebut posisi itu. Kalau posisi digit belum diketahui, petugas diminta membandingkan cetakan dari kiri ke kanan. Rincian asli tetap bisa dibuka. Demo dua KK fiktif memakai bahasa yang sama.

## 0.4.1 · 2026-11-24

Edisi dokumentasi untuk tim yang menerima repo. README sekarang langsung membuka demo dua KK. Panduan engineer mengikuti satu kasus dari bulk upload, koreksi NIK dan restart sampai uji ambang baca ulang. Kode aplikasi dan default routing sama dengan 0.4.0.

## 0.4.0 · 2026-11-22

Daftar berkas, form upload dan pemeriksaan NIK memakai bahasa Indonesia. Jumlah anggota di daftar diambil dari hasil yang sudah tersimpan. Di ponsel, tiap KK tampil sebagai kartu. Saat memeriksa NIK, petugas bisa melihat angka hasil ekstraksi dan potongan gambar di satu layar. Panduan latihan memakai nama tombol yang sama dengan UI.

## 0.3.0 · 2026-11-21

Latihan lokal memakai dua gambar. Tiap gambar mendapat job sendiri. Setelah mengoreksi satu NIK, kita bisa restart aplikasi, membuka nilai sebelum dan sesudah koreksi, lalu mencoba ambang baca ulang pada batch tadi. Default tetap 0,90.

Latihan berjalan dengan Python standard library dan SQLite. Hasil pembacaan, sinyal OCR, dan acuan cetakan disimpan terpisah. Edisi portabel lengkap juga disertakan di paket privat untuk latihan offline.

## 0.2.0 · 2026-11-21

Evaluasi sekarang mencakup semua field per KK. Laporan membedakan nilai yang terbaca, kolom kosong, field yang tidak ada di layout, dan field yang acuannya belum jelas. Contoh dua anggota menunjukkan kenapa satu field berbeda cukup membuat satu KK gagal cocok penuh.

## 0.1.0 · 2026-11-21

Rilis pertama untuk bulk upload dan pemeriksaan NIK.

- Tiap gambar dalam batch mendapat satu job dan bukti penerimaan. Aplikasi menangani pengiriman ulang.
- Worker membaca gambar, menjalankan OCR lokal bila diaktifkan, lalu meminta data keluarga dan anggota melalui adapter OCI Generative AI.
- Setelah hasil tersimpan, router membaca ulang crop NIK yang meragukan. Kasus review terikat ke anggota. Setiap keputusan petugas punya revisi sendiri sehingga hasil AI awal masih bisa dibuka.
- Untuk format cetak lama, router mencocokkan digit dengan crop. Digit yang hilang tetap perlu diperiksa. Validasi identitas untuk keperluan bisnis dilakukan terpisah.
- Pada instalasi baru, operator mengisi compartment dan routing profile lewat environment serta menyiapkan ledger untuk baca ulang. Repo portabel memakai contoh sintetis. Profil geometri customer disimpan secara privat.
