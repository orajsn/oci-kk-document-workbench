# Bulk upload dua KK, lalu cek satu NIK

Upload kartu A dan B sekaligus. Di kartu B, digit terakhir NIK anggota baris kedua salah baca. Dari contoh ini kita bisa cek dua hal. Apakah koreksinya masih ada setelah aplikasi di-restart? Kalau ambang baca ulang diubah, apakah digit yang salah tadi masih ketangkap?

Kedua kartu fiktif. Aplikasi memakai rekaman hasil baca dan skor OCR yang sudah disiapkan. UI, worker lokal, fungsi routing dan penyimpanan review berjalan dari repo ini. Untuk latihan cukup sediakan Python 3.11+ dan browser. Data disimpan di SQLite pada laptop.

## Jalankan aplikasi

Kalau memakai ZIP serah terima privat, ekstrak dan buka terminal di folder `portable-edition` yang berisi `run_local.py`. Kalau mengambil source dari GitHub, jalankan

```sh
git clone --branch v0.4.1 --depth 1 https://github.com/orajsn/oci-kk-document-workbench.git
cd oci-kk-document-workbench
```

Dari folder itu, jalankan

```sh
python --version
python run_local.py --port 8876 --data-dir runtime/day-one --reference-dir demo/day-one/reader
```

Di Windows bisa pakai `py -3`, di macOS/Linux bisa pakai `python3`. Gunakan interpreter yang sama sampai selesai. Biarkan terminal ini terbuka. Di [aplikasi lokal](http://127.0.0.1:8876), cek banner **Latihan lokal** dan pastikan **Rekaman latihan** bisa dipilih. Port 8876 untuk latihan ini. Contoh tunnel Batam memakai port 8765.

## Upload kedua gambar

1. Klik **Unggah KK**, isi **Nomor referensi** dengan `LATIHAN` dan pilih **Rekaman latihan**.
2. Dari `demo/day-one/`, pilih **kartu-a.png** dan **kartu-b.png** bersamaan dengan Ctrl atau Cmd. Klik **Unggah berkas** sekali.
3. Klik **Lihat unggahan**. Tunggu sampai kedua dokumen berstatus **Perlu diperiksa**. Case ID berakhir `-001` dan `-002`. Pakai nama file untuk mencari kartu B.
4. Buka terminal kedua di folder yang sama. Cek hasil batch dengan perintah berikut.

```sh
python examples/check_day_one.py --batch LATIHAN
```

Catat kedua job ID. Ringkasan **awal 2/3, tersimpan 2/3** berarti dua dari tiga NIK cocok dengan cetakan.

## Koreksi NIK kartu B

1. Buka **kartu-b.png**, pilih tab **Anggota**, lalu bandingkan baris 2 dengan gambar di kiri. Di cetakan, NIK berakhir **0009**. Di hasil ekstraksi, angkanya **0003**.
2. Ganti digit terakhir NIK pada baris itu menjadi **9**. Pertahankan nol di depan. Isi **Catatan petugas** dengan alasan koreksi. Pilih **Perlu diperiksa** karena baru satu field yang dicek, lalu klik **Simpan pemeriksaan**.
3. Tutup tab browser. Di terminal server tekan Ctrl+C, lalu ulangi perintah `run_local.py` dari langkah awal dengan `--data-dir` yang sama. Buka aplikasi di jendela privat browser, lalu buka **kartu-b.png → Anggota**. Digit akhirnya sekarang **9**.
4. Klik **Unduh JSON**. NIK hasil AI di `review_history[0].previous_extraction.members[1].nik_raw` masih berakhir **3**. NIK setelah koreksi di `extraction.members[1].nik_raw` berakhir **9**. Indeks `[1]` adalah anggota kedua.

Di terminal kedua, cek lagi batch tadi.

```sh
python examples/check_day_one.py --batch LATIHAN --require-review
```

Kedua job ID harus sama dengan sebelum restart. Kartu A punya nol review, kartu B punya satu review dan satu NIK berubah. Ringkasannya **awal 2/3, tersimpan 3/3**. Koreksi ada di `runtime/day-one/pilot.sqlite3`. Kalau rekaman sintetis dibaca lagi, hasil awalnya tetap 2/3.

## Coba ubah ambang baca ulang

Pakai hasil AI **sebelum koreksi** pada batch yang sama.

```sh
python examples/check_day_one.py --batch LATIHAN --require-review --compare 0.83 0.80
```

| Ambang skor OCR | NIK yang dibaca ulang | NIK salah yang lolos | NIK benar yang ikut dibaca ulang |
|---|---:|---:|---:|
| 0,90 (default) | 2 | 0 | 1 |
| 0,83 | 1 | 0 | 0 |
| 0,80 | 0 | 1 | 0 |

Pada kartu B, skor OCR sintetisnya 0,84 untuk bacaan yang benar dan 0,82 untuk yang salah. Dengan ambang 0,83, hanya NIK yang salah masuk daftar baca ulang. Coba `--compare 0.85` dan keduanya akan masuk lagi. Tabel ini menghitung pemicu baca ulang. Jumlah yang akhirnya perlu dicek petugas baru diketahui setelah crop dibaca. Replay lokal ini hanya membaca job dan ekspor tanpa memanggil model atau mengubah record.

Parameternya `min_confidence` pada [fungsi classify](../nik_router/routing.py). Router yang berjalan setelah hasil tersimpan memakai fungsi yang sama dengan default `OCR_MIN_CONFIDENCE`. Untuk mengubah default, buat branch, jalankan regresi dan tes pada sampel terpisah dengan label terverifikasi, lalu ajukan rilis. Tag terakhir yang disetujui menjadi titik rollback.

## Kalau hasilnya berbeda

| Yang terlihat | Yang perlu dicek |
|---|---|
| **Rekaman latihan** abu-abu | Jalankan server dari root repo dengan `--reference-dir demo/day-one/reader`. Folder itu harus berisi dua file `KK-*.json`. |
| `REFERENCE_NOT_FOUND` | Pakai PNG asli dari repo. Gambar yang disimpan ulang bisa punya hash berbeda. |
| Checker belum menemukan dua job | Tunggu worker selesai, lalu cek Case ID. Kalau `LATIHAN` sudah dipakai, coba referensi baru seperti `LATIHAN2` dan sesuaikan `--batch LATIHAN2`. |
| Port sudah dipakai | Ganti port di perintah server, di URL browser dan di checker. Misalnya, pakai `--port 8877`. |
| Setelah restart daftar kosong | Cek folder tempat server dijalankan dan lokasi `runtime/day-one/pilot.sqlite3`. |
| Skor tersimpan masih 2/3 | Buka kartu B, cek baris 2 dan lihat riwayat di **Unduh JSON** untuk memastikan koreksi tersimpan. |

Untuk mencoba antrean NIK per anggota dengan crop dan memeriksa keputusan langsung di Oracle Database, ikuti [uji bulk dan review di OCI](HANDOVER.md#uji-bulk-dan-review-di-oci). Untuk mengukur akurasi pada KK milik customer, tim perlu gambar berizin dan acuan yang dinilai terpisah. [Panduan tuning](TUNING.md)
