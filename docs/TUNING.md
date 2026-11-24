# Kalau hasil ekstraksi salah, mulai cek dari mana?

Buka dulu gambar dan hasil untuk anggota yang sama. Kalau crop NIK ternyata bergeser satu baris, perbaiki posisi crop. Kalau barisnya benar dan cetakannya jelas, bandingkan apa yang dibaca OCR, apa yang dikembalikan Gemini, dan apa yang akhirnya tersimpan. Dari situ baru kelihatan bagian mana yang perlu diubah.

## Titik tuning di repo

| Yang mau dicek | File | Yang bisa dicoba |
|---|---|---|
| Orientasi atau batas crop | [document_preprocess.py](../kkpilot/adapters/document_preprocess.py), [focused_source_views.py](../kkpilot/adapters/focused_source_views.py) | Bandingkan crop ukuran asli dan hasil resize dari piksel yang sama. Cek hash dan orientasi, lalu lihat apakah nama atau angka di baris sebelah ikut terpotong. |
| NIK masuk ke orang yang salah | [local_paddle_ocr.py](../kkpilot/adapters/local_paddle_ocr.py), [locator.py](../nik_router/locator.py) | Cari kolom NIK dari header. Cocokkan posisi NIK dengan nama pada baris yang sama. Kalau pemilik baris belum jelas, teruskan ke petugas. |
| Nilai terlihat tetapi hasil Gemini salah field atau berubah format | [gemini_contract.py](../kkpilot/adapters/gemini_contract.py), [gemini_focused_contract.py](../kkpilot/adapters/gemini_focused_contract.py), [oci_gemini.py](../kkpilot/adapters/oci_gemini.py) | Cek prompt dan schema yang dipanggil adapter. Pertahankan nilai mentah, tanda baca yang bermakna, dan urutan anggota. Kalau schema berubah, sesuaikan parser dan tesnya. |
| Terlalu banyak NIK dibaca ulang atau ada NIK salah yang lolos | [routing.py](../nik_router/routing.py), fungsi `classify` | Uji `OCR_MIN_CONFIDENCE`, default `0.90`. Angka ini skor dari pembaca OCR. Hitung berapa NIK benar yang ikut dibaca ulang dan berapa kesalahan yang lolos. |
| Bacaan dari crop berbeda dengan hasil awal | [reread.py](../nik_router/reread.py), [service.py](../nik_router/service.py), [legacy_print.py](../nik_router/legacy_print.py) | Model melihat crop tanpa angka tersimpan, tanggal lahir atau jawaban acuan. Kalau konflik struktur masih ada, kasus tetap perlu diperiksa. NIK cetak lama 13 digit perlu layout lama, nomor unik, pola yang berulang, dan bacaan crop yang jelas dengan digit persis sama. |
| Petugas sulit memastikan satu digit | [nik_review.py](../kkpilot/cloud/nik_review.py), [nik-review.js](../web/nik-review.js) | Tampilkan ukuran asli dan sediakan zoom. Simpan keputusan beserta revisinya. Sebelum memakai koreksi sebagai acuan evaluasi, periksa kebenarannya dan izin penggunaannya. |

## Coba dulu pada satu kasus

1. Pilih satu field yang bermasalah. Catat dugaan penyebab dan hasil yang diharapkan sebelum mencoba perubahan. Sertakan baris anggota di sebelahnya dan beberapa field yang sebelumnya sudah benar.
2. Siapkan gambar pengembangan dan sisihkan sampel lain untuk tes akhir (holdout). Sertakan variasi layout, foto miring, pencahayaan dan cetakan lama. Simpan gambar serta label secara privat. Catat hash sumber, aturan penyeragaman format, field yang belum jelas, serta versi prompt dan model. Hanya evaluator yang boleh membaca acuan.
3. Setelah hasil ekstraksi dikunci, hitung field cocok dari **seluruh slot**, nilai terbaca yang cocok dari **seluruh nilai terbaca**, dan berapa KK yang **seluruh field-nya cocok**. Laporkan juga slot tanpa acuan, NIK salah yang lolos dan kasus yang masuk ke petugas. Kalau dua pembaca memakai piksel yang sama, keduanya bisa salah pada digit yang sama.
4. Dari receipt tiap job, pisahkan waktu antre, ekstraksi, baca ulang, commit Oracle dan routing terakhir. Catat panggilan baru, token input/output, retry dan error. Tandai tahap yang memakai hasil tersimpan supaya biaya dan waktu replay bisa dibaca dengan benar.
5. Ajukan rilis kalau perbaikannya berulang pada holdout, data tetap masuk ke anggota yang benar, dan field penting yang sebelumnya cocok tetap cocok. Catat konfigurasi untuk rollback. Kalau sumber masih ambigu, minta petugas melihat crop atau minta gambar yang lebih jelas.

## Contoh ambang baca ulang

Jalankan `python examples/tuning_lab.py --compare 0.80 0.90`. Lab ini memakai fungsi klasifikasi yang sama dengan service dan tiga anggota fiktif. Label baru dipakai setelah prediksi selesai.

Pada ambang 0,80, satu NIK salah lolos. Pada 0,90, NIK itu ketangkap, tapi satu NIK benar ikut dibaca ulang. Hasil ini menjelaskan dampak perubahan ambang pada contoh tersebut. Untuk mencoba dengan job yang sudah di-upload, ikuti [latihan bulk upload dan koreksi](DAY-ONE.md). Checker memakai hasil sebelum koreksi. Jumlah NIK yang dibaca ulang dan jumlah keputusan petugas dicatat di tahap yang berbeda.

## Cara menghitung hasil per KK

```sh
python examples/score_documents.py --reference examples/synthetic-reference.json --predictions examples/synthetic-prediction.json
```

Contoh ini punya 12 field keluarga dan 18 field untuk tiap anggota. Dengan dua anggota, totalnya 48 slot. Satu field sengaja dibuat berbeda. Hasilnya **47/48 field cocok** dan **0/1 KK cocok seluruhnya**.

Pasangkan prediksi dengan acuan memakai ID dokumen dan SHA-256 gambar yang sama. Status field bisa `readable`, `blank`, `absent` atau `unknown`. Field kosong tetap ada dalam JSON dengan nilai `null`. Cek fungsi `normalized()` untuk aturan perbandingan tanggal dan karakter. Kalau aturannya berubah antarversi, catat perubahan itu sebelum membandingkan skor. Simpan jawaban acuan di evaluator, terpisah dari aplikasi ekstraksi.
