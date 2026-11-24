# Memastikan crop jatuh di baris anggota yang benar

Kalau NIK terbaca jelas tetapi ternyata milik anggota di sebelahnya, cek dulu posisi crop pada gambar asli. Mulai dari gambar yang diizinkan, periksa orientasi EXIF, lalu terapkan crop manual dan rotasi sesuai konfigurasi input. Cocokkan nomor baris, batas tabel, dan hash piksel. Setelah yakin anggotanya benar, baru nilai apakah digitnya terbaca.

## Coba dengan gambar latihan

```sh
python examples/make_focus_fixture.py --output runtime/focus-example.json
```

Perintah ini membagi gambar contoh menjadi dua area, menghitung hash yang dibutuhkan, lalu mengecek kedua crop dengan pembaca manifest yang dipakai aplikasi. Koordinat contoh hanya berlaku untuk gambar itu. Untuk layout customer, periksa posisi setiap anggota pada foto yang diizinkan. Simpan manifest dan SHA-256 di lokasi privat yang dibaca validator. Isi `reference_values_included` harus `false`.

## File mana yang mengatur apa?

**Area tabel untuk pembacaan terarah.** Manifest `kk-focused-source-geometry-1.0` menyimpan satu record per hash gambar sumber. Isinya mencakup aturan crop dan rotasi, ukuran serta hash piksel gambar setelah transformasi, lalu dua kotak berurutan, `name_table` dan `middle_member_table`. Setiap kotak memakai koordinat `[left, top, right, bottom]` dalam piksel pada gambar tersebut. Catat juga ukuran dan hash piksel masing-masing crop. Nilai `review_record_sha256` mengikat manifest ke catatan pemeriksaan posisi.

**Baris anggota untuk review NIK.** Di `nik_router/crop-profiles.json`, setiap hash sumber dipasangkan dengan alias, transformasi, `canonical_sha`, kotak kolom NIK dan nama, serta satu kotak per anggota yang posisinya relatif terhadap kolom NIK. Batas vertikal kolom nama dan NIK harus sama. Kalau profil belum tersedia, router mencari header NIK dan mencocokkan nama dengan baris OCR pada frame yang sudah diverifikasi. Kalau anggotanya masih belum jelas, teruskan ke petugas.

**File model OCR.** Pada manifest `paddle-server-models-1.0`, bagian `files` memasangkan path relatif dengan SHA-256. Untuk tiap model, catat hash `inference.json`, `inference.yml` dan `inference.pdiparams` setelah asal dan versi bobot diverifikasi. Konfigurasi aplikasi menyimpan lokasi dan hash manifest itu sendiri.

Saat membandingkan ukuran asli dengan zoom, pakai koordinat sumber dan transformasi yang sama. Kalau crop bergeser satu baris, catat sebagai salah anggota walaupun semua digitnya terbaca.
