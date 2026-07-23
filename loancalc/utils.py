import pdfplumber
import re
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# mapping bulan Indonesia → Inggris untuk parsing tanggal
bulan_map = {
    "Januari": "January",
    "Februari": "February",
    "Maret": "March",
    "April": "April",
    "Mei": "May",
    "Juni": "June",
    "Juli": "July",
    "Agustus": "August",
    "September": "September",
    "Oktober": "October",
    "November": "November",
    "Desember": "December",
}


def indo_to_datetime(s: str) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    for indo, eng in bulan_map.items():
        if indo.lower() in s.lower():
            s = re.sub(indo, eng, s, flags=re.IGNORECASE)
            break
    s = " ".join(s.strip().split())
    try:
        return datetime.strptime(s, "%d %B %Y")
    except Exception as e:
        logger.debug(f"Failed to parse date '{s}': {e}")
        return None


def normalize_number_string(s: str) -> float:
    if not s or not isinstance(s, str):
        return 0.0
    cleaned = s.strip()
    cleaned = re.sub(r"[Rp\s]", "", cleaned, flags=re.IGNORECASE)
    # Indonesian uses '.' thousand sep and ',' decimal
    cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except Exception:
        logger.debug(f"normalize_number_string gagal mengkonversi: {s} → '{cleaned}'")
        return 0.0


def calculate_loan_details(P: float, annual_rate: float, n_months: int) -> dict:
    if P <= 0 or n_months <= 0:
        return {
            "Monthly Payment": 0.0,
            "Total Payment": 0.0,
            "Total Interest": 0.0,
            "Annual Payment": 0.0,
            "Mortgage Constant": 0.0,
        }
    r = annual_rate / 12 / 100
    try:
        if r == 0:
            monthly_payment = P / n_months
        else:
            monthly_payment = P * (r * (1 + r) ** n_months) / ((1 + r) ** n_months - 1)
    except Exception:
        monthly_payment = 0.0
    total_payment = monthly_payment * n_months
    total_interest = total_payment - P
    annual_payment = monthly_payment * 12
    mortgage_constant = (monthly_payment / P) * 100 if P > 0 else 0.0
    return {
        "Monthly Payment": monthly_payment,
        "Total Payment": total_payment,
        "Total Interest": total_interest,
        "Annual Payment": annual_payment,
        "Mortgage Constant": mortgage_constant,
    }


def format_name_for_display(raw_name: str) -> str:
    if not raw_name or not isinstance(raw_name, str):
        return "TIDAK DITEMUKAN"
    parts = re.findall(r"[A-Za-z]+", raw_name)
    if not parts:
        return "TIDAK DITEMUKAN"
    selected = parts[:2] if len(parts) >= 2 else parts
    return " ".join(selected).upper()


def extract_name(pdf_path: str) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return "TIDAK DITEMUKAN"
            first_page = pdf.pages[0]
            text = first_page.extract_text() or ""
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            candidate = None

            for idx, line in enumerate(lines):
                if "nama sesuai identitas" in line.lower():
                    after_label = re.split(
                        r"nama sesuai identitas[:\-\s]*", line, flags=re.IGNORECASE
                    )[-1].strip()
                    if after_label:
                        candidate = after_label
                    if idx + 1 < len(lines) and lines[idx + 1].strip():
                        candidate = lines[idx + 1].strip()
                    break

            if not candidate:
                for line in lines:
                    m = re.search(
                        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", line
                    )
                    if m:
                        candidate = m.group(1)
                        break

            if not candidate:
                return "TIDAK DITEMUKAN"

            candidate = re.split(r"[\/\|\n,]", candidate)[0].strip()
            candidate = re.sub(
                r"\b(NIK|LAKI[- ]LAKI|PEREMPUAN|NPWP)\b", "", candidate, flags=re.IGNORECASE
            ).strip()
            return format_name_for_display(candidate)
    except Exception as e:
        logger.debug(f"extract_name error: {e}")
        return "TIDAK DITEMUKAN"


def _cluster_words_by_block(words: List[dict], y_tol: float = 5.0, x_gap: float = 15.0) -> List[List[dict]]:
    """
    Kelompokkan kata menjadi blok teks.

    Tahap 1: kelompokkan per BARIS berdasarkan posisi vertikal (top),
             seperti versi sebelumnya.
    Tahap 2: DALAM tiap baris, pecah lagi menjadi blok per KOLOM
             berdasarkan jarak horizontal antar kata (x_gap).
             Ini mencegah beberapa kolom berdampingan (mis. Pelapor /
             Cabang / Baki Debet / Tanggal Update yang berada pada baris
             yang sama) tergabung jadi satu blok raksasa.
    """
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: w["top"])

    lines: List[List[dict]] = []
    current_line = [sorted_words[0]]
    for w in sorted_words[1:]:
        prev = current_line[-1]
        if abs(w["top"] - prev["top"]) <= y_tol:
            current_line.append(w)
        else:
            lines.append(current_line)
            current_line = [w]
    if current_line:
        lines.append(current_line)

    blocks: List[List[dict]] = []
    for line in lines:
        line_sorted = sorted(line, key=lambda w: w["x0"])
        current_block = [line_sorted[0]]
        for w in line_sorted[1:]:
            prev = current_block[-1]
            prev_x1 = prev.get("x1", prev["x0"])
            gap = w["x0"] - prev_x1
            if gap <= x_gap:
                current_block.append(w)
            else:
                blocks.append(current_block)
                current_block = [w]
        if current_block:
            blocks.append(current_block)

    return blocks

def _words_to_text(words: List[dict]) -> str:
    sorted_by_x = sorted(words, key=lambda w: w["x0"])
    return " ".join(w["text"] for w in sorted_by_x)


def find_label_value(blocks_text: List[str], label: str, lookahead: int = 3) -> Optional[str]:
    for i, blk in enumerate(blocks_text):
        if re.search(rf"\b{re.escape(label)}\b", blk, flags=re.IGNORECASE):
            for j in range(i + 1, min(i + 1 + lookahead, len(blocks_text))):
                candidate = blocks_text[j].strip()
                if candidate:
                    return candidate
    return None

_KNOWN_HEADER_LABELS = ["pelapor", "cabang", "baki debet", "tanggal update"]


def _looks_like_label(text: str) -> bool:
    """True jika teks blok ini sebenarnya salah satu label header, bukan value."""
    t = text.strip().lower()
    return any(t == lbl or t.startswith(lbl) for lbl in _KNOWN_HEADER_LABELS)

def extract_header_row_values(blocks_info: List[dict]) -> Dict[str, str]:
    """
    Ekstrak Pelapor / Cabang / Baki Debet / Tanggal Update dari baris header
    Kredit/Pembiayaan menggunakan pendekatan KOLOM EKSPLISIT, bukan
    nearest-neighbor. Ini menghindari bug di mana teks panjang (nama bank)
    "menang" jarak dibanding value pendek (mis. "KPO") saat dicari dengan
    heuristik jarak biasa.
    """
    label_order = ["pelapor", "cabang", "baki debet", "tanggal update"]

    found = []
    for b in blocks_info:
        t = b["lower"].strip()
        for lbl in label_order:
            if t == lbl or t == lbl + ":":
                found.append((lbl, b))
                break

    if len(found) < 2:
        return {}

    found.sort(key=lambda x: x[1]["top"])
    best_group = []
    for _, b in found:
        group = [item for item in found if abs(item[1]["top"] - b["top"]) <= 5]
        if len(group) > len(best_group):
            best_group = group

    if len(best_group) < 2:
        return {}

    label_row_top = sum(item[1]["top"] for item in best_group) / len(best_group)

    best_group.sort(key=lambda item: item[1]["x0"])
    boundaries = [item[1]["x0"] for item in best_group] + [float("inf")]

    value_row_candidates = [
        b for b in blocks_info
        if 0 < (b["top"] - label_row_top) <= 15
        and not _looks_like_label(b["text"])
    ]

    if not value_row_candidates:
        return {}

    result: Dict[str, str] = {}
    for i, (lbl, _) in enumerate(best_group):
        low = boundaries[i]
        high = boundaries[i + 1]
        col_blocks = [b for b in value_row_candidates if low <= b["x0"] < high]
        if col_blocks:
            col_blocks.sort(key=lambda b: b["x0"])
            result[lbl] = " ".join(b["text"] for b in col_blocks).strip()

    return result


def extract_value_under_or_right(label: str, blocks_info: List[dict], y_tol: float = 3, x_tol: float = 150) -> Optional[str]:
    for blk in blocks_info:
        if re.search(rf"\b{re.escape(label)}\b", blk["text"], flags=re.IGNORECASE):
            base_top = blk["top"]
            base_x0 = blk["x0"]
            right_candidates = []
            below_candidates = []
            for other in blocks_info:
                if other is blk:
                    continue
                if _looks_like_label(other["text"]):
                    continue
                if abs(other["top"] - base_top) <= y_tol and other["x0"] > base_x0 and (other["x0"] - base_x0) < x_tol:
                    right_candidates.append((other["x0"], other["text"]))
                if 0 < other["top"] - base_top <= 15 and abs(other["x0"] - base_x0) <= x_tol:
                    below_candidates.append((other["top"], other["text"]))
            if below_candidates:
                below_candidates.sort(key=lambda x: x[0])
                return below_candidates[0][1].strip()
            if right_candidates:
                right_candidates.sort(key=lambda x: x[0])
                return right_candidates[0][1].strip()
    return None

def extract_kualitas_number(kualitas_raw: str) -> str:
    """Ambil angka pertama dari string kualitas, mis. '2 - Dalam Perhatian Khusus' -> '2'."""
    if not kualitas_raw or kualitas_raw == "TIDAK DITEMUKAN":
        return ""
    m = re.match(r"\s*(\d+)", kualitas_raw)
    return m.group(1) if m else ""
def extract_kualitas_bulan_tahun(blocks_info: List[dict], kualitas_num: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Ekstrak histori Kualitas/Jumlah Hari Tunggakan dari grid bulan/tahun.

    Strategi:
    1. Temukan semua header bulan/tahun (mis. 'Agt 23', 'Jul 24')
    2. Kelompokkan header per baris berdasarkan posisi vertikal (top)
    3. Untuk tiap baris header, cari blok data DI BAWAH baris tersebut saja
       (bukan overlap antar baris)
    4. Tangani format blok gabungan '2 82' (kualitas + hari dalam 1 blok)
    5. Pilih bulan paling kanan di baris paling akhir (paling baru) dengan kualitas != 1
    """
    if not kualitas_num:
        logger.debug("extract_kualitas_bulan_tahun: kualitas_num is empty")
        return None, None

    month_header_pattern = re.compile(
        r"^(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agt|Sep|Okt|Nov|Des)\s*'?\s*(\d{2})$",
        flags=re.IGNORECASE,
    )

    # --- 1. Temukan semua header bulan dan catat id-nya ---
    headers = []
    header_block_ids: set = set()
    for b in blocks_info:
        m = month_header_pattern.match(b["text"].strip())
        if m:
            headers.append({
                "label": f"{m.group(1).title()} {m.group(2)}",
                "top": b["top"],
                "x0": b["x0"],
            })
            header_block_ids.add(id(b))

    if not headers:
        logger.debug("No month headers found")
        return None, None

    logger.debug(f"Found {len(headers)} month headers")

    # --- 2. Kelompokkan header per baris (y-position clustering, tol=5pt) ---
    headers_by_top = sorted(headers, key=lambda h: h["top"])
    header_rows: List[List[dict]] = []
    current_row = [headers_by_top[0]]
    for h in headers_by_top[1:]:
        if abs(h["top"] - current_row[-1]["top"]) <= 5:
            current_row.append(h)
        else:
            header_rows.append(current_row)
            current_row = [h]
    header_rows.append(current_row)

    logger.debug(f"Header rows detected: {len(header_rows)}")
    for ri, rh in enumerate(header_rows):
        logger.debug(f"  Row {ri}: top={rh[0]['top']:.1f}, cols={[h['label'] for h in rh]}")

    matched_columns = []

    # --- 3. Proses tiap baris header secara terpisah ---
    for row_idx, row_headers in enumerate(header_rows):
        row_top = sum(h["top"] for h in row_headers) / len(row_headers)

        # Batas atas data: tepat di bawah baris header ini
        data_top_min = row_top + 8

        # Batas bawah data: tepat di atas baris header berikutnya, atau +50
        if row_idx + 1 < len(header_rows):
            next_row_top = min(h["top"] for h in header_rows[row_idx + 1])
            data_top_max = next_row_top - 3
        else:
            data_top_max = row_top + 50

        # Rentang x: dari kolom paling kiri - 25 sampai paling kanan + 80
        row_x0_min = min(h["x0"] for h in row_headers) - 25
        row_x0_max = max(h["x0"] for h in row_headers) + 80

        # Ambil blok data di area ini:
        # - BUKAN header bulan
        # - Mengandung digit
        # - Tidak mengandung huruf (menyaring "1 - Lancar", "Rp xxx", dll.)
        row_data_blocks = [
            b for b in blocks_info
            if id(b) not in header_block_ids
            and data_top_min <= b["top"] <= data_top_max
            and row_x0_min <= b["x0"] <= row_x0_max
            and re.search(r"\d", b["text"])
            and not re.search(r"[A-Za-z]", b["text"])  # hanya angka / spasi / karakter non-huruf
        ]

        logger.debug(f"Row {row_idx} data area: top=[{data_top_min:.1f},{data_top_max:.1f}], "
                     f"blocks found={len(row_data_blocks)}")
        for db in row_data_blocks:
            logger.debug(f"  data block: '{db['text']}' x0={db['x0']:.1f} top={db['top']:.1f}")

        row_headers_sorted = sorted(row_headers, key=lambda h: h["x0"])

        for i, header in enumerate(row_headers_sorted):
            left_bound = header["x0"] - 20
            right_bound = (
                row_headers_sorted[i + 1]["x0"] - 20
                if i + 1 < len(row_headers_sorted)
                else header["x0"] + 80
            )

            col_data = [
                b for b in row_data_blocks
                if left_bound <= b["x0"] < right_bound
            ]

            if not col_data:
                continue

            col_data_sorted = sorted(col_data, key=lambda b: b["x0"])

            # Ekstrak kualitas_val dan hari_val
            kualitas_val: Optional[str] = None
            hari_val: Optional[str] = None

            if len(col_data_sorted) == 1:
                # Format gabungan: '1 0' atau '2 82'
                parts = col_data_sorted[0]["text"].strip().split()
                digits_only = [p for p in parts if re.match(r"^\d+$", p)]
                if len(digits_only) >= 2:
                    kualitas_val = digits_only[0]
                    hari_val = digits_only[1]
                elif len(digits_only) == 1:
                    kualitas_val = digits_only[0]
            else:
                # Format terpisah: blok kiri = kualitas, blok kanan = hari
                k_parts = col_data_sorted[0]["text"].strip().split()
                h_parts = col_data_sorted[1]["text"].strip().split()
                k_digits = [p for p in k_parts if re.match(r"^\d+$", p)]
                h_digits = [p for p in h_parts if re.match(r"^\d+$", p)]
                if k_digits:
                    kualitas_val = k_digits[0]
                if h_digits:
                    hari_val = h_digits[0]

            if not kualitas_val:
                continue

            logger.debug(f"Row {row_idx} Column {header['label']}: "
                         f"kualitas='{kualitas_val}', hari='{hari_val}'")

            # Match: kolom kiri SELAIN angka 1
            if kualitas_val != "1" and hari_val is not None:
                # sort_key: baris lebih akhir (row_idx besar) dan kolom lebih kanan (x0 besar)
                sort_key = row_idx * 100000 + int(header["x0"])
                matched_columns.append((sort_key, header["label"], kualitas_val, hari_val))
                logger.debug(f"  ✓ MATCH! (kualitas={kualitas_val} != 1, hari={hari_val})")

    if not matched_columns:
        logger.debug("No columns found with kualitas selain 1")
        return None, None

    # Pilih bulan paling baru (baris terbesar + paling kanan dalam baris)
    best = max(matched_columns, key=lambda x: x[0])
    best_label, best_hari = best[1], best[3]

    logger.debug(f"Selected (latest row, rightmost): {best_label} - {best_hari} hari")
    return best_label, best_hari

def extract_kualitas_value(blocks_info: List[dict]) -> str:
    """
    Ambil nilai 'Kualitas' di tabel detail.
    Menangani 2 kasus:
    1. Label & value tergabung dalam satu blok (mis. "No Rekening Kualitas 1 - Lancar")
    2. Label & value berada di blok terpisah (kanan/bawah)
    """
    for b in blocks_info:
        txt = b["lower"]
        if "kualitas" in txt and "jumlah hari tunggakan" not in txt:
            m = re.search(
                r"kualitas\s*[:\-]?\s*(\d\s*-\s*[A-Za-z\s]+)",
                b["text"],
                flags=re.IGNORECASE,
            )
            if m:
                result = m.group(1).strip()
                logger.debug(f"Kualitas (same-block): '{result}'")
                return result

    for b in blocks_info:
        txt = b["lower"].strip()

        if txt == "kualitas" or txt.startswith("kualitas:"):
            if "jumlah hari tunggakan" in txt:
                logger.debug(f"Skipped header grid: {txt}")
                continue

            candidates = [
                o for o in blocks_info
                if o is not b
                and abs(o["top"] - b["top"]) <= 10
                and o["x0"] > b["x0"]
                and (o["x0"] - b["x0"]) < 600
            ]

            if candidates:
                candidates.sort(key=lambda o: o["x0"])
                result = candidates[0]["text"].strip()
                logger.debug(f"Kualitas (right): '{result}'")
                return result

            below_candidates = [
                o for o in blocks_info
                if o is not b
                and 0 < (o["top"] - b["top"]) <= 15
                and abs(o["x0"] - b["x0"]) <= 200
            ]
            if below_candidates:
                below_candidates.sort(key=lambda o: o["top"])
                result = below_candidates[0]["text"].strip()
                logger.debug(f"Kualitas (below): '{result}'")
                return result

            logger.debug(f"Label kualitas found but no value nearby")

    logger.debug(f"Kualitas not found")
    return "TIDAK DITEMUKAN"


def extract_tanggal_update(blocks_info: List[dict]) -> Tuple[Optional[datetime], str]:
    for b in blocks_info:
        if "tanggal update" not in b["lower"]:
            continue

        m = re.search(
            r"tanggal\s*update\s*[:\-]?\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4})",
            b["text"],
            flags=re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            return indo_to_datetime(raw), raw

        below_candidates = [
            o for o in blocks_info
            if o is not b
            and not _looks_like_label(o["text"])
            and 0 < (o["top"] - b["top"]) <= 15
            and abs(o["x0"] - b["x0"]) <= 150
        ]
        if below_candidates:
            below_candidates.sort(key=lambda o: o["top"])
            raw = below_candidates[0]["text"].strip()
            if raw:
                return indo_to_datetime(raw), raw

        right_candidates = [
            o for o in blocks_info
            if o is not b
            and not _looks_like_label(o["text"])
            and abs(o["top"] - b["top"]) <= 5 and o["x0"] > b["x0"]
        ]
        if right_candidates:
            right_candidates.sort(key=lambda o: o["x0"])
            raw = right_candidates[0]["text"].strip()
            if raw:
                return indo_to_datetime(raw), raw

    return None, "TIDAK DITEMUKAN"

def is_kualitas_bermasalah(kualitas_raw: str) -> bool:
    """True jika kualitas BUKAN '1 - Lancar' (atau varian yang diawali angka 1)."""
    if not kualitas_raw or kualitas_raw == "TIDAK DITEMUKAN":
        return False
    return not kualitas_raw.strip().startswith("1")

def clean_entity_text(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    cleaned = raw
    cleaned = re.sub(r"Rp[\s\d\.,]+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b", "", cleaned)
    cleaned = re.sub(r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", "", cleaned)
    cleaned = " ".join(cleaned.strip().split())
    return cleaned


def disambiguate_bank_and_branch(bank_raw: str, cabang_raw: str) -> Tuple[str, str]:
    bank = bank_raw or "TIDAK DITEMUKAN"
    cabang = cabang_raw or "TIDAK DITEMUKAN"

    bank = clean_entity_text(bank)
    cabang = clean_entity_text(cabang)

    bank_norm = bank.strip().lower()
    cabang_norm = cabang.strip().lower()

    if cabang_norm and cabang_norm in bank_norm and bank_norm != cabang_norm:
        pattern = re.escape(cabang.strip())
        cleaned_bank = re.sub(pattern, "", bank, flags=re.IGNORECASE).strip()
        if cleaned_bank:
            bank = cleaned_bank

    if bank.strip().lower() == cabang.strip().lower():
        branch_indicators = [" KPO", " KC ", " KCP", " MEDAN", " SURABAYA", " JAKARTA", " SBY"]
        lowered = bank.lower()
        for ind in branch_indicators:
            if ind.lower().strip() in lowered:
                idx = lowered.find(ind.lower().strip())
                if idx != -1:
                    possible_bank = bank[:idx].strip()
                    possible_branch = bank[idx:].strip()
                    if possible_bank:
                        bank = possible_bank
                    if possible_branch:
                        cabang = possible_branch
                    break

    return bank.strip(), cabang.strip()


def matches_exclusion(value: str, excludes: List[str]) -> bool:
    if not value:
        return False
    for ex in excludes:
        if not ex:
            continue
        pattern = rf"\b{re.escape(ex.strip())}\b"
        if re.search(pattern, value, flags=re.IGNORECASE):
            return True
    return False


def gather_nearby_text(target_block: dict, blocks_info: List[dict], vertical_tol: float = 10, horizontal_tol: float = 200) -> str:
    pieces = [target_block["text"]]
    for b in blocks_info:
        if b is target_block:
            continue
        if abs(b["top"] - target_block["top"]) <= vertical_tol or abs(b["x0"] - target_block["x0"]) <= horizontal_tol:
            pieces.append(b["text"])
    return " ".join(pieces)


def extract_active_facilities(
    pdf_path: str,
    excluded_banks: List[str],
    excluded_branches: List[str],
) -> List[dict]:
    facilities = []
    seen = set()
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                raw_text = page.extract_text() or ""
                lower_raw = raw_text.lower()

                words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
                if not words:
                    continue

                blocks_raw = _cluster_words_by_block(words, y_tol=3.0)
                blocks_info = []
                for block in blocks_raw:
                    text = _words_to_text(block)
                    tops = [w["top"] for w in block]
                    x0s = [w["x0"] for w in block]
                    x1s = [w.get("x1", w["x0"]) for w in block]
                    blocks_info.append({
                        "text": text,
                        "lower": text.lower(),
                        "top": sum(tops) / len(tops),
                        "x0": min(x0s),     
                        "x1": max(x1s),     
                        "raw": block,
                    })

                # deteksi kondisi aktif eksplisit (skip kalau ada "lunas")
                kondisi_targets = []
                for b in blocks_info:
                    if "kondisi" in b["lower"]:
                        window = b["lower"]
                        for other in blocks_info:
                            if abs(other["top"] - b["top"]) <= 15:
                                window += " " + other["lower"]
                        if ("fasilitas aktif" in window) or re.search(r"fasilitas\s+aktif", window):
                            if re.search(r"kondisi[\s\S]{0,30}lunas", window):
                                logger.debug(f"Page {page_num} skip kondisi lunas: {window}")
                                continue
                            kondisi_targets.append(b)

                # fallback if phrase exists but no explicit condition block
                fallback_mode = False
                if not kondisi_targets and "fasilitas aktif" in lower_raw:
                    fallback_mode = True

                # konteks Kredit/Pembiayaan
                kredit_blocks = [b for b in blocks_info if "kredit/pembiayaan" in b["lower"]]

                iter_targets = kondisi_targets if kondisi_targets else ([{"lower": "", "top": 0, "x0": 0, "text": ""}] if fallback_mode else [])
                if not iter_targets:
                    continue

                for cond_blk in iter_targets:
                    raw_bank = "TIDAK DITEMUKAN"
                    raw_cabang = "TIDAK DITEMUKAN"
                    header_tanggal_update_raw = None

                    header_values = extract_header_row_values(blocks_info)
                    if header_values.get("pelapor"):
                        raw_bank = header_values["pelapor"]
                    if header_values.get("cabang"):
                        raw_cabang = header_values["cabang"]
                    if header_values.get("tanggal update"):
                        header_tanggal_update_raw = header_values["tanggal update"]

                    if (raw_bank == "TIDAK DITEMUKAN" or raw_cabang == "TIDAK DITEMUKAN") and kredit_blocks:
                        base_kredit = sorted(kredit_blocks, key=lambda x: x["top"])[0]
                        context_blocks = [
                            b for b in blocks_info
                            if b["top"] >= base_kredit["top"] and b["top"] <= base_kredit["top"] + 120
                        ]
                        if raw_bank == "TIDAK DITEMUKAN":
                            bank_candidate = extract_value_under_or_right("Pelapor", context_blocks)
                            if bank_candidate:
                                raw_bank = bank_candidate
                        if raw_cabang == "TIDAK DITEMUKAN":
                            cabang_candidate = extract_value_under_or_right("Cabang", context_blocks)
                            if cabang_candidate:
                                raw_cabang = cabang_candidate

                    # fallback global
                    if raw_bank == "TIDAK DITEMUKAN":
                        raw_bank = extract_value_under_or_right("Pelapor", blocks_info) or find_label_value(
                            [b["text"] for b in blocks_info], "Pelapor"
                        ) or "TIDAK DITEMUKAN"
                    if raw_cabang == "TIDAK DITEMUKAN":
                        raw_cabang = extract_value_under_or_right("Cabang", blocks_info) or find_label_value(
                            [b["text"] for b in blocks_info], "Cabang"
                        ) or "TIDAK DITEMUKAN"

                    bank, cabang = disambiguate_bank_and_branch(raw_bank, raw_cabang)

                    if matches_exclusion(bank, excluded_banks):
                        logger.debug(f"Page {page_num}: bank '{bank}' dikecualikan.")
                        continue
                    if matches_exclusion(cabang, excluded_branches):
                        logger.debug(f"Page {page_num}: cabang '{cabang}' dikecualikan.")
                        continue

                    # ekstraksi Plafon Awal
                    loan_amount = 0.0
                    for b in blocks_info:
                        if "plafon awal" in b["lower"]:
                            window_plafon = gather_nearby_text(b, blocks_info)
                            m_plafon = re.search(
                                r"Plafon\s*Awal\s*[:\-]?\s*(?:Rp\s*)?([\d\.,]+)",
                                window_plafon,
                                flags=re.IGNORECASE,
                            )
                            if m_plafon:
                                loan_amount = normalize_number_string(m_plafon.group(1))
                                break
                    if loan_amount == 0.0:
                        m_plafon_full = re.search(
                            r"Plafon\s*Awal\s*[:\-]?\s*(?:Rp\s*)?([\d\.,]+)",
                            raw_text,
                            flags=re.IGNORECASE,
                        )
                        if m_plafon_full:
                            loan_amount = normalize_number_string(m_plafon_full.group(1))

                    # ekstraksi interest rate
                    interest_rate = 0.0
                    for b in blocks_info:
                        if "suku bunga" in b["lower"] or "imbalan" in b["lower"]:
                            window_rate = gather_nearby_text(b, blocks_info)
                            m_rate = re.search(
                                r"(?:Suku\s*Bunga(?:/Imbalan)?|Imbalan)\s*[:\-]?\s*([\d\.,]+)\s*%?",
                                window_rate,
                                flags=re.IGNORECASE,
                            )
                            if m_rate:
                                interest_rate = float(m_rate.group(1).replace(",", "."))
                                break
                    if interest_rate == 0.0:
                        m_rate_full = re.search(
                            r"(?:Suku\s*Bunga(?:/Imbalan)?|Imbalan)\s*[:\-]?\s*([\d\.,]+)\s*%?",
                            raw_text,
                            flags=re.IGNORECASE,
                        )
                        if m_rate_full:
                            interest_rate = float(m_rate_full.group(1).replace(",", "."))

                    # ekstraksi tanggal
                    start_dt = None
                    end_dt = None
                    for b in blocks_info:
                        if "tanggal awal kredit" in b["lower"]:
                            window_start = gather_nearby_text(b, blocks_info)
                            m_start = re.search(
                                r"Tanggal\s*Awal\s*Kredit\s*[:\-]?\s*([\d]{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)",
                                window_start,
                                flags=re.IGNORECASE,
                            )
                            if m_start:
                                start_dt = indo_to_datetime(m_start.group(1))
                                break
                    if not start_dt:
                        fallback_start = re.search(
                            r"Tanggal\s*Awal\s*Kredit\s*[:\-]?\s*([\d]{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)",
                            raw_text,
                            flags=re.IGNORECASE,
                        )
                        if fallback_start:
                            start_dt = indo_to_datetime(fallback_start.group(1))

                    for b in blocks_info:
                        if "tanggal jatuh tempo" in b["lower"]:
                            window_end = gather_nearby_text(b, blocks_info)
                            m_end = re.search(
                                r"Tanggal\s*Jatuh\s*Tempo\s*[:\-]?\s*([\d]{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)",
                                window_end,
                                flags=re.IGNORECASE,
                            )
                            if m_end:
                                end_dt = indo_to_datetime(m_end.group(1))
                                break
                    if not end_dt:
                        fallback_end = re.search(
                            r"Tanggal\s*Jatuh\s*Tempo\s*[:\-]?\s*([\d]{1,2}\s+[A-Za-z]+(?:\s+\d{4})?)",
                            raw_text,
                            flags=re.IGNORECASE,
                        )
                        if fallback_end:
                            end_dt = indo_to_datetime(fallback_end.group(1))

                    loan_term_months = 0
                    if start_dt and end_dt:
                        loan_term_months = (end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month)
                        if end_dt.day < start_dt.day:
                            loan_term_months -= 1
                        loan_term_months = max(0, loan_term_months)

                    key = (
                        bank.strip().lower(),
                        cabang.strip().lower(),
                        loan_amount,
                        loan_term_months,
                        page_num,
                    )
                    if key in seen:
                        continue
                    seen.add(key)

                    kualitas_raw = extract_kualitas_value(blocks_info)

                    kualitas_num = extract_kualitas_number(kualitas_raw)
                    bulan_label, hari_tunggakan = extract_kualitas_bulan_tahun(blocks_info, kualitas_num)
                    if bulan_label and hari_tunggakan:
                        bulan_tahun_display = f"{bulan_label} - {hari_tunggakan} hari"
                    else:
                        bulan_tahun_display = "TIDAK DITEMUKAN"
                    
                    if header_tanggal_update_raw:
                        tanggal_update_dt = indo_to_datetime(header_tanggal_update_raw)
                        tanggal_update_raw = header_tanggal_update_raw
                    else:
                        tanggal_update_dt, tanggal_update_raw = extract_tanggal_update(blocks_info)

                    facilities.append({
                        "bank": bank,
                        "cabang": cabang,
                        "loan_amount": loan_amount,
                        "interest_rate": interest_rate,
                        "start_date": start_dt,
                        "end_date": end_dt,
                        "loan_term_months": loan_term_months,
                        "kualitas": kualitas_raw,
                        "bulan_tahun": bulan_tahun_display, 
                        "tanggal_update": tanggal_update_dt,
                        "tanggal_update_raw": tanggal_update_raw,
                        "page": page_num,
                    })
    except Exception as e:
        logger.exception(f"extract_active_facilities error: {e}")

    return facilities


def extract_slik_data(
    pdf_path: str,
    excluded_banks: List[str],
    excluded_branches: List[str]
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    nama_raw = extract_name(pdf_path)
    nama = format_name_for_display(nama_raw)
    facilities = extract_active_facilities(pdf_path, excluded_banks, excluded_branches)

    # determine if PDF had extractable text: simple heuristic
    has_text = True
    if not facilities:
        # possible that PDF has no text or no active facilities
        # try to detect if any text exists at all
        try:
            with pdfplumber.open(pdf_path) as pdf:
                any_text = "".join(p.extract_text() or "" for p in pdf.pages)
                if not any_text.strip():
                    has_text = False
        except Exception:
            has_text = False

    rows = []
    total_monthly_payment = 0.0
    for idx, fac in enumerate(facilities, start=1):
        term = fac.get("loan_term_months", 0) or 0
        details = calculate_loan_details(
            P=fac.get("loan_amount", 0.0),
            annual_rate=fac.get("interest_rate", 0.0),
            n_months=term,
        )
        monthly_payment = details["Monthly Payment"]
        total_monthly_payment += monthly_payment
        rows.append({
            "no": idx,
            "nama": nama,
            "Nama": nama,
            "bank": fac.get("bank"),
            "cabang": fac.get("cabang"),
            "kualitas": fac.get("kualitas", "TIDAK DITEMUKAN"),
            "bulan_tahun": fac.get("bulan_tahun", "TIDAK DITEMUKAN"),  
            "tanggal_update": fac.get("tanggal_update_raw", "TIDAK DITEMUKAN"),
            "loan_amount": fac.get("loan_amount"),
            "interest_rate": fac.get("interest_rate"),
            "loan_term_months": term,
            "start_date": fac.get("start_date"),
            "end_date": fac.get("end_date"),
            "monthly_payment": monthly_payment,
            "total_payment": details["Total Payment"],
            "total_interest": details["Total Interest"],
            "annual_payment": details["Annual Payment"],
            "mortgage_constant": details["Mortgage Constant"],
            "page": fac.get("page"),
        })

    df = pd.DataFrame(rows)
    meta = {
        "has_text": has_text,
        "facility_count": len(rows),
        "total_monthly_payment": round(total_monthly_payment, 2),
    }
    return df, meta


def calculate_credit_analysis(
    status_pekerjaan: str = "tetap",
    penghasilan_bersih: float = 0.0,
    harga_jual: float = 0.0,
    uang_muka: float = 0.0,
    suku_bunga: float = 7.5,
    bunga_floating: float = 12.0,
    tenor_tahun: int = 15,
    total_monthly_slik: float = 0.0,
    problem_facilities_count: int = 0
) -> dict:
    status_clean = str(status_pekerjaan).lower()
    if status_clean == "kontrak":
        persentase_gaji = 35.0
        label_pekerjaan = "Pegawai Kontrak (35%)"
    elif status_clean == "pengusaha":
        persentase_gaji = 35.0
        label_pekerjaan = "Pengusaha (35%)"
    else:
        persentase_gaji = 55.0
        label_pekerjaan = "Pegawai Tetap (55%)"

    # 1. Gaji yang diakui = Penghasilan * Persentase (55% / 35%)
    gaji_diakui = penghasilan_bersih * (persentase_gaji / 100.0)
    
    # 2. Kapasitas Bulanan = Gaji Diakui - Total Hutang SLIK
    kapasitas_bulanan = gaji_diakui - total_monthly_slik
    
    # 3. Kapasitas Tahunan = Kapasitas Bulanan * 12
    kapasitas_tahunan = kapasitas_bulanan * 12.0
    
    # 4. Maksimal Plafon KPR = Kapasitas Tahunan / Bunga Floating
    bunga_floating_val = float(bunga_floating) if bunga_floating and float(bunga_floating) > 0 else 12.0
    rate_floating_decimal = bunga_floating_val / 100.0
    
    if kapasitas_tahunan > 0 and rate_floating_decimal > 0:
        maks_plafon_kpr = kapasitas_tahunan / rate_floating_decimal
    else:
        maks_plafon_kpr = 0.0

    # Nilai KPR Diminta (Harga Jual - Uang Muka)
    nilai_kpr = max(0.0, harga_jual - uang_muka)
    
    # Angsuran KPR Baru (Metode Anuitas)
    n_months = int(tenor_tahun * 12) if tenor_tahun and tenor_tahun > 0 else 180
    loan_calc = calculate_loan_details(nilai_kpr, suku_bunga, n_months)
    angsuran_kpr_baru = loan_calc.get("Monthly Payment", 0.0)
    
    ltv_ratio = (nilai_kpr / harga_jual * 100.0) if harga_jual > 0 else 0.0
    dp_percent = (uang_muka / harga_jual * 100.0) if harga_jual > 0 else 0.0
    
    # --- Evaluasi 1: Karakter ---
    if problem_facilities_count == 0:
        karakter_status = "LANCAR (BAIK)"
        karakter_badge = "success"
        karakter_desc = "Tidak ditemukan catatan kredit bermasalah pada SLIK OJK."
    else:
        karakter_status = f"BERMASALAH ({problem_facilities_count} Fasilitas)"
        karakter_badge = "danger"
        karakter_desc = f"Terdapat {problem_facilities_count} fasilitas kredit berstatus tunggakan/kualitas bermasalah."

    # --- Evaluasi 2: Kapasitas ---
    selisih_plafon = maks_plafon_kpr - nilai_kpr
    selisih_angsuran = kapasitas_bulanan - angsuran_kpr_baru
    
    if penghasilan_bersih <= 0:
        kapasitas_status = "BELUM DIISI"
        kapasitas_badge = "secondary"
        kapasitas_desc = "Masukkan nilai penghasilan bersih untuk menghitung kapasitas angsuran."
    elif selisih_plafon >= 0 and selisih_angsuran >= 0:
        kapasitas_status = "MEMENUHI (LAYAK)"
        kapasitas_badge = "success"
        kapasitas_desc = f"Maksimal Plafon KPR (Rp {maks_plafon_kpr:,.0f}) mencukupi untuk Plafon KPR yang diminta (Rp {nilai_kpr:,.0f})."
    else:
        kapasitas_status = "DEFISIT (TIDAK MEMENUHI)"
        kapasitas_badge = "danger"
        kapasitas_desc = f"Nilai KPR yang diminta (Rp {nilai_kpr:,.0f}) melebihi Maksimal Plafon KPR (Rp {maks_plafon_kpr:,.0f})."

    # --- Evaluasi 3: Kolateral ---
    if harga_jual <= 0:
        kolateral_status = "BELUM DIISI"
        kolateral_badge = "secondary"
        kolateral_desc = "Masukkan harga jual properti untuk menghitung LTV Ratio."
    elif ltv_ratio <= 85.0:
        kolateral_status = "MEMENUHI (LTV IDEAL)"
        kolateral_badge = "success"
        kolateral_desc = f"LTV Ratio sebesar {ltv_ratio:.2f}% (DP {dp_percent:.2f}%). Nilai agunan mencukupi."
    else:
        kolateral_status = "RISIKO TINGGI (LTV > 85%)"
        kolateral_badge = "warning"
        kolateral_desc = f"LTV Ratio sebesar {ltv_ratio:.2f}% (DP {dp_percent:.2f}%). Uang muka disarankan minimal 15-20%."

    is_layak = (problem_facilities_count == 0) and (selisih_plafon >= 0) and (ltv_ratio <= 85.0) and (penghasilan_bersih > 0) and (harga_jual > 0)

    return {
        "status_pekerjaan": status_clean,
        "label_pekerjaan": label_pekerjaan,
        "persentase_gaji": persentase_gaji,
        "penghasilan_bersih": penghasilan_bersih,
        "gaji_diakui": gaji_diakui,
        "harga_jual": harga_jual,
        "uang_muka": uang_muka,
        "nilai_kpr": nilai_kpr,
        "suku_bunga": suku_bunga,
        "bunga_floating": bunga_floating_val,
        "tenor_tahun": tenor_tahun,
        "tenor_bulan": n_months,
        "total_monthly_slik": total_monthly_slik,
        "kapasitas_bulanan": kapasitas_bulanan,
        "kapasitas_tahunan": kapasitas_tahunan,
        "maks_plafon_kpr": maks_plafon_kpr,
        "angsuran_kpr_baru": angsuran_kpr_baru,
        "selisih_plafon": selisih_plafon,
        "selisih_angsuran": selisih_angsuran,
        "ltv_ratio": ltv_ratio,
        "dp_percent": dp_percent,
        "karakter": {
            "status": karakter_status,
            "badge": karakter_badge,
            "desc": karakter_desc,
        },
        "kapasitas": {
            "status": kapasitas_status,
            "badge": kapasitas_badge,
            "desc": kapasitas_desc,
        },
        "kolateral": {
            "status": kolateral_status,
            "badge": kolateral_badge,
            "desc": kolateral_desc,
        },
        "is_layak": is_layak,
    }
