# Tuning yang bisa dilakukan tim aplikasi

Mulai dari satu field yang salah dan catat letak kegagalannya. Angka yang keliru dari crop baris tetangga harus diperbaiki pada geometri. Menambah instruksi ejaan pada prompt tidak memulihkan baris anggota yang tertukar.

| Tahap | Lokasi di repo | Contoh perubahan dan gate |
|---|---|---|
| Persiapan sumber | [document_preprocess.py](../kkpilot/adapters/document_preprocess.py), [focused_source_views.py](../kkpilot/adapters/focused_source_views.py) | Pertahankan orientasi dan hash piksel. Bandingkan native versus resize pada crop yang sama. Uji nama atau angka tetangga agar tidak ikut terpotong. |
| OCR dan pengikatan baris | [local_paddle_ocr.py](../kkpilot/adapters/local_paddle_ocr.py), [locator.py](../nik_router/locator.py) | Pastikan header, nama dan NIK berada di baris yang sama; bila lokasi gambar meragukan, route ke petugas. |
| Pembacaan Gemini | [gemini_contract.py](../kkpilot/adapters/gemini_contract.py), [gemini_focused_contract.py](../kkpilot/adapters/gemini_focused_contract.py), [oci_gemini.py](../kkpilot/adapters/oci_gemini.py) | Edit instruksi dan schema yang benar-benar dipanggil oleh adapter. Pertahankan field mentah, tanda baca bermakna dan urutan anggota. Ubah parser serta pengujian bila schema berubah. |
| Pemicu baca ulang | [routing.py](../nik_router/routing.py), fungsi `classify` | Nilai default `OCR_MIN_CONFIDENCE` adalah 0.90. Confidence OCR ialah skor recognizer. Ubah secara terisolasi, lalu ukur konsekuensinya bagi petugas dan error yang lolos. |
| Konfirmasi dari crop | [reread.py](../nik_router/reread.py), [service.py](../nik_router/service.py), [legacy_print.py](../nik_router/legacy_print.py) | Model menerima crop tanpa angka tersimpan, tanggal lahir atau kunci jawaban. Tetap pertahankan konflik struktural sampai ada dukungan yang sesuai. Nomor 13 digit hanya dapat dikonfirmasi sebagai cetakan lama pada layout lama, unik, dengan pola berulang dan pembacaan jelas yang digitnya identik. |
| Keputusan petugas | [nik_review.py](../kkpilot/cloud/nik_review.py), [nik-review.js](../web/nik-review.js) | Tampilkan sumber pada ukuran asli, beri kontrol zoom dan simpan event berversi. Koreksi petugas dipakai sebagai data evaluasi hanya jika telah diverifikasi dan penggunaannya diizinkan. |

### Cara menjalankan satu eksperimen

1. Pilih kohor pengembangan dan holdout dengan variasi layout, kemiringan, pencahayaan, cetakan lama dan keterbacaan. Simpan sumber dan label secara privat. Bekukan hash gambar, kebijakan format, status belum jelas, versi prompt dan model.
2. Uji hipotesis pada field yang membedakan metode baru. Pisahkan prediksi dari evaluator. Sertakan kontrol anggota yang berdekatan dan nilai benar yang sebelumnya lolos.
3. Hitung **field cocok / semua slot**, **nilai terbaca cocok / nilai terbaca**, **dokumen dengan semua slot cocok**, dan **slot tanpa acuan**. Laporkan review burden dan error escape secara terpisah dari akurasi. Persetujuan dua model yang melihat sumber sama bukan verifikasi independen.
4. Ukur waktu antre, ekstraksi, reread, commit dan routing akhir. Ambil token input/output, jumlah panggilan, retry dan error dari receipt yang bersangkutan. Pisahkan stage yang memakai ulang cache dari panggilan baru.
5. Rekomendasikan rilis bila keuntungan berulang pada data holdout tanpa salah pasangan anggota atau regresi field penting; catat konfigurasi rollback. Jika bukti gambar tetap tidak cukup, minta crop ditinjau atau gambar yang lebih jelas.

### Lab lokal

`python examples/tuning_lab.py --compare 0.80 0.90` memanggil classifier yang sama dengan service terhadap data rekaan. Label demonstrasi dipakai sesudah prediksi. Hasil lab menjelaskan trade-off ambang, bukan benchmark performa OCR atau klaim layanan Gemini.

Pada fixture tiga anggota, ambang 0,80 melepaskan satu nomor yang berbeda dari label. Ambang 0,90 menangkapnya dan juga menandai satu bacaan yang sebenarnya benar. Laporkan kedua efek ini saat mengajukan tuning.

### Rekap seluruh KK

```sh
python examples/score_documents.py --reference examples/synthetic-reference.json --predictions examples/synthetic-prediction.json
```

Fixture memuat satu KK fiktif, 12 field keluarga dan 18 field per anggota. Pada dua anggota ada 48 slot. Contoh prediksi sengaja salah pada satu field: 47/48 match dan 0/1 dokumen cocok penuh. Sediakan satu referensi dan prediksi per ID dokumen dengan SHA-256 sumber yang sama. Status per field adalah `readable`, `blank`, `absent` atau `unknown`. Tanggal dan karakter sumber mengikuti `normalized()`; tandai perubahan kebijakan perbandingan sebelum memakai angka pada rilis berbeda. File harus tetap memuat field kosong dengan nilai JSON null. Jalankan evaluator setelah keluaran dikunci. Kunci jawaban tidak diperlukan oleh aplikasi ekstraksi.
