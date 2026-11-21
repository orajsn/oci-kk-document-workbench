# Satu batch, satu koreksi, satu eksperimen NIK

Pada kartu latihan B, satu digit NIK berbeda dari cetakan. Unggah kedua kartu, perbaiki digit itu, lalu gunakan batch yang sama untuk menguji kapan NIK perlu dibaca ulang.

Latihan ini memakai gambar fiktif, pembacaan dan skor OCR sintetis yang dibekukan, serta SQLite di laptop. UI, worker lokal, penyimpanan review dan fungsi routing dijalankan dari kode repo. Perintah di bawah cukup dengan Python 3.11+ dan browser. Tidak ada paket pip atau akun cloud yang perlu disiapkan.

## 1. Mulai dari folder kosong

Jika menerima ZIP serah terima privat, ekstrak ZIP lalu buka terminal di `portable-edition`, di sebelah `run_local.py`. Jika memakai GitHub, jalankan:

```sh
git clone --branch v0.4.0 --depth 1 https://github.com/orajsn/oci-kk-document-workbench.git
cd oci-kk-document-workbench
```

Dari folder itu, jalankan:

```sh
python --version
python run_local.py --port 8876 --data-dir runtime/day-one --reference-dir demo/day-one/reader
```

Di Windows Anda juga bisa memakai `py -3`, atau `python3` di macOS/Linux. Pakai interpreter yang sama di semua langkah. Biarkan terminal pertama terbuka. Buka [workspace lokal](http://127.0.0.1:8876). Banner **Latihan lokal** dan mode **Rekaman latihan** yang bisa dipilih menunjukkan Anda membuka layanan latihan. Port ini terpisah dari contoh tunnel Batam di port 8765.

## 2. Upload kedua kartu sekaligus

1. Klik **Unggah KK**. Isi Nomor referensi `LATIHAN` dan pilih **Rekaman latihan**.
2. Di pemilih file, masuk ke `demo/day-one`. Pilih **kartu-a.png** dan **kartu-b.png** sekaligus dengan Ctrl atau Cmd. Klik **Unggah berkas** satu kali.
3. Klik **Lihat unggahan** setelah unggahan selesai. Tunggu kedua dokumen berstatus **Perlu diperiksa**. Case ID memiliki akhiran `-001` dan `-002`. Gunakan nama file untuk mengenali kartunya.
4. Buka terminal kedua di folder yang sama dan jalankan:

```sh
python examples/check_day_one.py --batch LATIHAN
```

Anda akan melihat dua job ID. Sebelum review, keluaran **awal 2/3, tersimpan 2/3** berarti dua dari tiga NIK cocok dengan cetakan. Salin kedua ID jika ingin mencocokkannya setelah restart.

## 3. Koreksi satu NIK dan buka kembali

1. Buka **kartu-b.png** di daftar dokumen. Bandingkan gambar di sebelah kiri dengan tab **Anggota**. Pada baris 2, sumber berakhir **0009** dan nilai tersimpan berakhir **0003**.
2. Ubah digit terakhir field NIK pada **baris 2** menjadi **9**. Pertahankan seluruh nol di depan. Isi **Catatan petugas** dengan alasan singkat, pilih **Perlu diperiksa** karena Anda baru memeriksa satu field, lalu klik **Simpan pemeriksaan**. Riwayat mendapatkan satu keputusan.
3. Tutup tab browser. Di terminal server tekan Ctrl+C, lalu jalankan kembali perintah server pada langkah 1 **dengan data-dir yang sama**. Buka workspace di jendela privat browser, buka **kartu-b.png → Anggota**. Digit akhirnya harus **9**. Klik **Unduh JSON**. Dalam `review_history[0].previous_extraction.members[1].nik_raw` digit akhirnya masih **3**. `extraction.members[1].nik_raw` berakhir **9**. Array JSON dimulai dari indeks 0.
4. Jalankan pemeriksaan berikut di terminal kedua:

```sh
python examples/check_day_one.py --batch LATIHAN --require-review
```

**Tanda lulus.** Dua job ID tetap sama. Kartu B memiliki satu review dan satu NIK berubah. Kartu A memiliki nol review. Ringkasan menjadi **awal 2/3, tersimpan 3/3**. Koreksi disimpan di `runtime/day-one/pilot.sqlite3`. Pembaca sintetis tetap menghasilkan nilai awalnya.

## 4. Tuning satu field

Pertanyaannya: pada bacaan NIK yang sama, apakah ada ambang yang mengirim lebih sedikit NIK benar untuk baca ulang tanpa melewatkan kesalahan pada kartu B? Uji kandidat terhadap keluaran **sebelum koreksi**.

```sh
python examples/check_day_one.py --batch LATIHAN --require-review --compare 0.83 0.80
```

| Ambang bukti OCR | NIK ke baca ulang | NIK salah lolos | NIK benar ikut baca ulang |
|---|---:|---:|---:|
| 0,90 · default | 2 | 0 | 1 |
| 0,83 · kandidat latihan | 1 | 0 | 0 |
| 0,80 · pembanding | 0 | 1 | 0 |

Pada kartu B, OCR buatan memberi skor 0,84 untuk bacaan yang cocok dan 0,82 untuk bacaan yang keliru. Ambang 0,83 memisahkan keduanya. Angka di tabel adalah **pemicu baca ulang**. Beban review manusia baru bisa diukur sesudah pembaca crop mengembalikan hasil. Tidak ada model yang dipanggil selama replay ini.

**Coba sendiri:** ganti argumen pembanding menjadi `--compare 0.85`. Kedua NIK kartu B kembali masuk daftar baca ulang. Kode yang dipakai ada di [fungsi classify](../nik_router/routing.py), parameter `min_confidence`. Router pascapenyimpanan memakai default `OCR_MIN_CONFIDENCE` pada fungsi yang sama. Untuk mengusulkan default baru, ubah konstanta itu pada branch tersendiri, jalankan uji regresi dan evaluasi holdout dengan label terverifikasi, lalu ajukan rilis. Gunakan tag terakhir yang disetujui untuk rollback. Replay ini hanya membaca job dan ekspor, sehingga mencoba ambang lain tidak mengubah record.

## Kalau hasil Anda berbeda

| Gejala | Cek berikutnya |
|---|---|
| Rekaman latihan berwarna abu-abu | Jalankan server dari root repo dengan `--reference-dir demo/day-one/reader`. Pastikan root itu berisi dua file `demo/day-one/reader/KK-*.json`. |
| `REFERENCE_NOT_FOUND` | Gunakan PNG asli dari repo. Menyimpan kembali gambar dapat mengubah hash meski tampilannya sama. |
| Perintah tidak menemukan dua job | Tunggu worker selesai, lalu cocokkan Case ID pada daftar. Jika prefix sudah terpakai, buat satu batch baru dengan prefix berbeda, misalnya `LATIHAN2`, lalu gunakan `--batch LATIHAN2`. |
| Server tidak dapat bind ke port | Pilih port lain seperti 8877 pada server, URL browser dan `--port 8877` pada checker. |
| Daftar kosong setelah restart | Jalankan dari folder repo yang sama. Periksa lokasi `runtime/day-one/pilot.sqlite3`. |
| Hasil akhir masih 2/3 | Buka kartu B, cek baris, pastikan simpan berhasil. Klik **Unduh JSON** dari record tersimpan untuk memeriksa riwayatnya. |

## Penerimaan di OCI

Latihan lokal membuktikan bulk, review dokumen dan tuning routing. Antrean NIK per anggota dengan crop dan penyimpanan keputusan di Oracle tersedia pada deployment cloud. Untuk mengujinya pada lingkungan penerima, ikuti [uji bulk dan review di OCI](HANDOVER.md#uji-bulk-dan-review-di-oci). Untuk angka kualitas customer, gunakan kohor berizin dan evaluator terpisah sesuai [panduan tuning](TUNING.md).
