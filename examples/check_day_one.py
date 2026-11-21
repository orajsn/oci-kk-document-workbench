"""Check a local training batch and replay its original NIKs through the workbench router."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nik_router.routing import classify, OCR_MIN_CONFIDENCE

CARDS = ROOT / "demo" / "day-one"


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_batch(port, batch):
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(route):
        with opener.open(f"http://127.0.0.1:{port}/api/{route}", timeout=5) as response:
            return json.load(response)

    config = read("config")
    if config.get("local_only") is not True or config.get("live_oci_enabled") is not False:
        raise ValueError("Gunakan server run_local.py pada port latihan.")
    pattern = re.compile(re.escape(batch) + r"-00[12]")
    jobs = [j for j in read("jobs?" + urlencode({"query": batch}))["jobs"] if pattern.fullmatch(j["case_id"])]
    if len(jobs) != 2 or len({j["case_id"] for j in jobs}) != 2:
        raise ValueError("Perlu satu batch unik berisi kedua kartu. Periksa Case ID atau gunakan prefix baru.")
    if any(j["mode"] != "reference" or j["status"] not in {"NEEDS_REVIEW","ACCEPTED"} for j in jobs):
        raise ValueError("Tunggu kedua hasil Reference replay selesai. Periksa status dan mode di daftar.")
    return [read(f"jobs/{j['id']}/export") for j in jobs]


def rows(extraction):
    members = extraction["members"]
    numbers = {str(m["row_number"]): m["nik_raw"] for m in members}
    if len(numbers) != len(members):
        raise ValueError("Nomor anggota muncul dua kali.")
    return numbers


def report(exports, thresholds, require_review):
    signals = load_json(CARDS / "signals.json")
    key = load_json(CARDS / "print-key.json")
    by_source = {e["job"]["sha256"]: e for e in exports}
    if set(by_source) != set(signals) or set(key) != set(signals):
        raise ValueError("Hash batch berbeda dari dua kartu latihan.")
    counts = {t: [0, 0, 0] for t in thresholds}
    verified_edits = original_hits = final_hits = slots = 0
    for digest, evidence in signals.items():
        job = by_source[digest]
        if hashlib.sha256((CARDS / evidence["filename"]).read_bytes()).hexdigest() != digest:
            raise ValueError("Gambar latihan lokal berubah.")
        history = job["review_history"]
        last = job["extraction"]
        original = history[0]["previous_extraction"] if history else last
        raw = rows(original)
        final = rows(last)
        if raw != rows(evidence["primary"]) or set(raw) != set(key[digest]) or set(final) != set(raw):
            raise ValueError("Baris hasil awal atau nomor anggota berubah. Periksa batch.")
        for field, expected in key[digest].items():
            slots += 1
            original_hits += raw[field] == expected
            final_hits += final[field] == expected
            if final[field] != raw[field] and final[field] == expected and history:
                verified_edits += 1
        changes = sum(final[n] != v for n, v in raw.items())
        print(f"{evidence['filename']} | {job['job']['case_id']} | job {job['job']['id']} | "
              f"versi {job['job']['version']} | {len(history)} review | {changes} NIK berubah")
        for threshold, tally in counts.items():
            flags = classify(original, evidence["primary"], evidence["hybrid"], evidence["ocr"], 0,
                             min_confidence=threshold)
            for flag in flags:
                n = str(flag["row"])
                wrong = raw[n] != key[digest][n]
                reread = flag["primary"]
                tally[0] += reread
                tally[1] += wrong and not reread
                tally[2] += not wrong and reread
    print(f"NIK cocok dengan cetakan: awal {original_hits}/{slots}, tersimpan {final_hits}/{slots}.")
    print("Ambang OCR | NIK ke baca ulang | Salah lolos | Benar ikut baca ulang")
    for t, tally in counts.items():
        print(f"{t:.2f}       | {tally[0]:17} | {tally[1]:11} | {tally[2]:21}")
    if require_review and (verified_edits != 1 or final_hits != slots):
        raise ValueError("Koreksi NIK di kartu B sesuai cetakan, simpan, lalu jalankan lagi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--batch", default="LATIHAN")
    parser.add_argument("--compare", nargs="+", type=float, default=[0.83, 0.80],
                        help="Ambang pembanding. Default router selalu ditampilkan.")
    parser.add_argument("--require-review", action="store_true")
    args = parser.parse_args()
    try:
        report(read_batch(args.port, args.batch), [OCR_MIN_CONFIDENCE, *args.compare], args.require_review)
    except (ValueError, URLError, OSError, KeyError) as exc:
        parser.exit(1, f"Latihan belum lulus: {exc}\n")
