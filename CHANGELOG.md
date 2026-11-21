# Changelog

## 0.2.0 · 2026-11-21

Tambahkan evaluasi semua field per dokumen. Hasil meliputi slot terbaca, kosong atau tidak berlaku, perbedaan, acuan ambigu dan KK yang cocok penuh. Fixture sintetis menunjukkan dampak satu field yang salah pada skor satu dokumen.

## 0.1.0 · 2026-11-21

Rilis handover awal dari workbench bulk dan router review.

- Bulk upload memberi satu job untuk tiap dokumen, dengan receipt dan penanganan pengiriman ulang.
- Worker membaca citra, menjalankan OCR lokal opsional, lalu memanggil adapter OCI Generative AI dengan kontrak data rumah tangga dan anggota.
- Pemeriksaan post-commit membaca ulang crop NIK yang meragukan dan membuat kasus per anggota. Keputusan manusia berversi disimpan terpisah dari payload AI.
- Dukungan format cetak lama membandingkan digit dengan bukti crop tanpa mengisi nol atau menebak digit. Validasi identitas bisnis merupakan tahap tersendiri.
- Distribusi portabel mengambil compartment dan routing profile dari environment operator. Profil geometri customer dikeluarkan, ledger baca ulang dapat diinisialisasi pada instalasi baru, dan demo serta lab memakai fixture sintetis.
