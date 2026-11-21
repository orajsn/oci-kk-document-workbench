# Changelog

## 0.3.0 · 2026-11-21

Tambahkan latihan penerima dari dua gambar ke dua job, koreksi satu NIK, restart, audit versi dan pembandingan ambang routing pada batch yang diunggah. Pembacaan sintetis, sinyal OCR dan kunci cetakan disimpan terpisah. Jalur lokal memakai SQLite dan Python standard library. Default routing tetap 0,90. Paket privat memuat edisi portabel lengkap agar latihan dapat diikuti secara offline.

## 0.2.0 · 2026-11-21

Tambahkan evaluasi semua field per dokumen. Hasil meliputi slot terbaca, kosong atau tidak berlaku, perbedaan, acuan ambigu dan KK yang cocok penuh. Fixture sintetis menunjukkan dampak satu field yang salah pada skor satu dokumen.

## 0.1.0 · 2026-11-21

Rilis handover awal dari workbench bulk dan router review.

- Bulk upload memberi satu job untuk tiap dokumen, dengan receipt dan penanganan pengiriman ulang.
- Worker membaca citra, menjalankan OCR lokal opsional, lalu memanggil adapter OCI Generative AI dengan kontrak data rumah tangga dan anggota.
- Pemeriksaan post-commit membaca ulang crop NIK yang meragukan dan membuat kasus per anggota. Keputusan manusia berversi disimpan terpisah dari payload AI.
- Dukungan format cetak lama membandingkan digit dengan bukti crop tanpa mengisi nol atau menebak digit. Validasi identitas bisnis merupakan tahap tersendiri.
- Distribusi portabel mengambil compartment dan routing profile dari environment operator. Profil geometri customer dikeluarkan, ledger baca ulang dapat diinisialisasi pada instalasi baru, dan demo serta lab memakai fixture sintetis.
