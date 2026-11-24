# Terraform untuk pilot KK

Template ini mulai dengan pembuatan resource dinonaktifkan. Buat workspace Terraform terpisah di akun operator. Baca `variables.tf`, lalu salin `pilot.tfvars.example` ke file privat untuk tenancy yang akan dipakai.

Sebelum menyalakan resource berbayar, cek region, routing, alokasi CIDR, availability domain, kapasitas shape dan referensi secret database. Jalankan `terraform init`, `terraform validate` dan `terraform plan` di workspace tadi. Cocokkan resource di plan dengan yang disetujui. Simpan Terraform state dan kredensial provider di lingkungan tim.

Setelah infrastrukturnya siap, pasang aplikasi dan router secara terpisah sesuai [panduan operasi](../docs/OPERATIONS.md). Aplikasi bind ke loopback dan diakses melalui SSH privat. Kalau nanti memakai listener atau load balancer, autentikasi per pengguna tetap perlu disiapkan.
