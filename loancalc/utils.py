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


def _cluster_words_by_block(words: List[dict], y_tol: float = 5.0) -> List[List[dict]]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w["top"])
    blocks = []
    current = [sorted_words[0]]
    for w in sorted_words[1:]:
        prev = current[-1]
        if abs(w["top"] - prev["top"]) <= y_tol:
            current.append(w)
        else:
            blocks.append(current)
            current = [w]
    if current:
        blocks.append(current)
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
                if abs(other["top"] - base_top) <= y_tol and other["x0"] > base_x0 and (other["x0"] - base_x0) < x_tol:
                    right_candidates.append((other["x0"], other["text"]))
                if 0 < other["top"] - base_top <= 15 and abs(other["x0"] - base_x0) <= x_tol:
                    below_candidates.append((other["top"], other["text"]))
            if right_candidates:
                right_candidates.sort(key=lambda x: x[0])
                return right_candidates[0][1].strip()
            if below_candidates:
                below_candidates.sort(key=lambda x: x[0])
                return below_candidates[0][1].strip()
    return None


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
                    xs = [w["x0"] for w in block]
                    blocks_info.append({
                        "text": text,
                        "lower": text.lower(),
                        "top": sum(tops) / len(tops),
                        "x0": sum(xs) / len(xs),
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

                    # prioritas dari section Kredit/Pembiayaan
                    if kredit_blocks:
                        base_kredit = sorted(kredit_blocks, key=lambda x: x["top"])[0]
                        context_blocks = [
                            b for b in blocks_info
                            if b["top"] >= base_kredit["top"] and b["top"] <= base_kredit["top"] + 120
                        ]
                        bank_candidate = extract_value_under_or_right("Pelapor", context_blocks)
                        cabang_candidate = extract_value_under_or_right("Cabang", context_blocks)
                        if bank_candidate:
                            raw_bank = bank_candidate
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

                    facilities.append({
                        "bank": bank,
                        "cabang": cabang,
                        "loan_amount": loan_amount,
                        "interest_rate": interest_rate,
                        "start_date": start_dt,
                        "end_date": end_dt,
                        "loan_term_months": loan_term_months,
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