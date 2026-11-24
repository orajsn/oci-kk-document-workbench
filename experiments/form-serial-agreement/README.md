# Kapan awalan nomor seri bisa disimpan otomatis?

Kadang angka nomor seri sudah terbaca, tapi huruf di sebelah kirinya hilang dari hasil ekstraksi. Di branch staging ini, aplikasi mengecek lagi tiga bagian pada baris cetakan yang sama. Label, huruf seri dan angka dibaca terpisah. Kalau hasilnya mendukung, aplikasi bisa melengkapi `household.form_serial` sebelum penyimpanan. Aturan ini hanya berlaku untuk seri formulir. NIK anggota punya alur pemeriksaan sendiri.

## Syaratnya apa?

Angka pada hasil tersimpan, ekstraksi utama dan hasil hybrid harus sama persis, termasuk nol di depan. Di bagian kiri header, OCR halaman harus menemukan satu pasangan label dan angka yang bersebelahan. Pastikan posisi itu bisa dipetakan kembali ke piksel asli pada job yang sama.

OCR CPU membaca masing-masing potongan pada ukuran asli dan 3× tanpa melihat jawaban acuan. Dua bacaan label harus mengenali label nomor. Kedua bacaan huruf harus sama persis dengan awalan dari OCR halaman, dan kedua bacaan angka harus sama persis dengan angka semula. Kalau satu syarat gagal atau field itu sudah punya flag review, fungsi mengembalikan hasil yang diterima sebelumnya.

## Mencoba dan memasang modul

Dari folder ini jalankan `python -m unittest -v test_serial_agreement` dengan Python 3.9+. Tes keputusan dan geometri memakai standard library. Untuk memotong gambar di pipeline OCR, `prefix_cells.py` memakai Pillow dan NumPy.

1. Panggil `split_serial.pair(saved, primary, hybrid, ocr, frame)`. Ukuran frame dan koordinat OCR harus cocok. Bentuk input bisa dilihat di `test_serial_agreement.py`.
2. Cocokkan gambar sumber, transformasi dan koordinat dengan job. Kalau koreksi perspektif membuat lokasi crop tidak bisa dipetakan kembali, hentikan baca ulang. Ambil potongan label dan huruf lewat `prefix_cells.cut(native_image, proposal['label_box'])`. Ambil angka di `proposal['number_box']`.
3. Baca tiap potongan pada ukuran asli, lalu 3× dengan interpolasi bicubic. Isi `reads` dengan kunci `label`, `series` dan `number`. Tiap kunci berisi dua objek `{'text': ...}` sesuai urutan ukuran tadi.
4. Panggil `serial_agreement.apply(saved, proposal, reads)` sebelum hasil disimpan. Aplikasi pemanggil tetap mengurus antrean review dan commit ke Oracle Database. Simpan hasil awal, identitas sumber dan bukti crop secara privat untuk audit. Pemeriksaan ini memakai OCR lokal tanpa panggilan Gemini.

## Kalau dua bacaan sama, bisa tetap salah?

Bisa. Gambar 1× dan 3× berasal dari piksel yang sama dan dibaca oleh OCR yang sama. Kalau OCR halaman dan kedua crop sama-sama salah mengenali huruf, kesalahan itu bisa lolos. Sebelum aturan staging ini dipakai lebih luas, uji foto lain dengan variasi awalan dan cek mana yang salah tetapi ikut tersimpan.
