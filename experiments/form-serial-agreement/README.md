# Awalan Nomor Seri Formulir

Modul staging ini melengkapi nomor seri sebelum hasil ekstraksi disimpan. Modul memerlukan hasil ekstraksi utama, hasil hybrid, OCR halaman beserta koordinat, dan dua pembacaan per potongan gambar. Modul ini dapat diuji tanpa akun cloud.

## Jalankan tes

Pakai Python 3.9 atau lebih baru. Dari folder ini jalankan `python -m unittest -v test_serial_agreement`. Tes keputusan dan geometri hanya memakai standard library Python. Pemotongan piksel pada `prefix_cells.py` memakai Pillow dan NumPy yang tersedia di pipeline OCR.

## Sambungkan ke aplikasi

1. Panggil `split_serial.pair(saved, primary, hybrid, ocr, frame)`. Format data contoh ada di `test_serial_agreement.py`. Koordinat OCR harus sesuai dengan ukuran frame. Kandidat hanya muncul jika tiga nilai numerik sama dan pada bagian kiri header ada satu pasangan label dan angka yang bersebelahan.
2. Cocokkan sumber, transformasi, dan koordinat dengan gambar asli untuk job tersebut. Jika gambar telah diluruskan dengan perspektif dan koordinat tidak dapat dipetakan kembali, hentikan pembacaan tambahan. Baca dua potongan dari `prefix_cells.cut(native_image, proposal['label_box'])` serta potongan di `proposal['number_box']`.
3. Baca masing-masing potongan pada ukuran asli lalu pembesaran bicubic 3× dengan OCR CPU tanpa memberikan teks acuan. Kirim hasil dengan kunci `label`, `series`, dan `number`; tiap nilai memuat dua objek `{'text': ...}`, sesuai urutan ukuran.
4. Panggil `serial_agreement.apply(saved, proposal, reads)` sebelum persistensi. Respons baru hanya dapat melengkapi `household.form_serial`. Digit termasuk nol di awal harus identik. Bila bukti gagal, nilai sebelumnya diteruskan ke alur review aplikasi. Keputusan review yang sudah ada pada field ini menghalangi koreksi otomatis.
5. Simpan hasil awal, identitas sumber dan bukti crop secara privat untuk audit. Modul ini tidak memanggil Gemini, mengatur antrean review, atau menulis ke Oracle Database.

## Batas bukti

Kedua pembesaran memakai piksel sumber dan OCR yang sama. Jika OCR halaman dan kedua crop sama-sama keliru, kesalahan itu dapat lolos. Uji pada lebih banyak formulir dengan variasi awalan sebelum mengaktifkan aturan secara umum. Modul ini hanya menangani nomor seri formulir; data identitas anggota tidak disentuh.
