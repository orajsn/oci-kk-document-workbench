# Kalibrasi crop dan model OCR

**Input pertama adalah gambar asli yang disetujui.** Periksa orientasi EXIF, lalu crop manual dan rotasi sesuai kebijakan input. Nomor baris, batas tabel dan hash piksel mengikat bukti ke pemilik field. Diagnosis geometri terpisah dari keputusan apakah karakter terbaca.

### Gambar sintetis untuk mempelajari kontrak

```sh
python examples/make_focus_fixture.py --output runtime/focus-example.json
```

Perintah ini membagi gambar contoh menjadi dua area ilustrasi, menghitung tanda pengenal dan pin, lalu memanggil pembaca manifest asli untuk memeriksa kedua crop. Koordinat pada contoh hanya cocok untuk gambar demonstrasi. Untuk deployment aktif, buat profil dengan foto privat yang diizinkan dan review posisi anggota per layout. Simpan manifest dan sha256-nya pada lokasi privat yang disyaratkan oleh validator. `reference_values_included` selalu false.

### Tiga kontrak berbeda

1. **Focused view.** Manifest `kk-focused-source-geometry-1.0` memuat satu record per hash sumber, kebijakan crop/rotasi, ukuran/pixel hash kanonis dan dua bbox berurutan: `name_table` dan `middle_member_table`. Setiap bbox memakai [left, top, right, bottom] pixel kanonis, berikut ukuran dan hash piksel ROI. `review_record_sha256` mengikat bukti review geometri.
2. **Router NIK.** `nik_router/crop-profiles.json` memetakan hash sumber ke alias, transform, `canonical_sha`, rectangle kolom NIK dan kolom nama, dan satu bbox relatif kolom NIK per anggota. Kedua kolom harus berbagi koordinat vertikal yang sama. Jika profil tidak tersedia, router mencoba header NIK dan pasangan nama/baris OCR pada frame yang diverifikasi. Crop yang tidak dapat diikat secara aman perlu pemeriksaan operator.
3. **Model OCR.** `paddle-server-models-1.0` berisi `files`, yaitu pemetaan path relatif ke SHA-256. Untuk setiap model, hash `inference.json`, `inference.yml` dan `inference.pdiparams`. Hanya buat manifest dari bobot yang telah diverifikasi asal dan versinya. Konfigurasi menunjuk manifest serta hash file manifest itu sendiri.

Ukuran bbox selalu merujuk citra sumber dengan transform yang dicatat. Pembesaran hanyalah view baru. Bila satu crop berpindah ke anggota lain, laporkan sebagai salah pemetaan walaupun semua digit yang terbaca valid.
