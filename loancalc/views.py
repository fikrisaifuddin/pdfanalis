import os
import tempfile
import logging
from typing import List
from datetime import datetime
import json

import time
from django.views.decorators.http import require_POST

from django.shortcuts import render
from django.http import HttpRequest, HttpResponse

from .utils import extract_slik_data, extract_name, is_kualitas_bermasalah, calculate_credit_analysis, serialize_result_for_export
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(tempfile.gettempdir(), "slik_pdf_cache")
CACHE_MAX_AGE_SECONDS = 2 * 60 * 60  # sapu file yatim (browser crash, dll.)


def _purge_expired_cache() -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        now = time.time()
        for fname in os.listdir(CACHE_DIR):
            fpath = os.path.join(CACHE_DIR, fname)
            try:
                if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > CACHE_MAX_AGE_SECONDS:
                    os.remove(fpath)
            except Exception:
                logger.warning("Gagal menghapus cache lama: %s", fpath)
    except Exception:
        logger.warning("Gagal membersihkan cache PDF")


def _is_safe_cache_path(path: str) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(CACHE_DIR)]) == os.path.abspath(CACHE_DIR)
    except Exception:
        return False


def _clear_cached_pdf(request: HttpRequest) -> None:
    path = request.session.pop("cached_pdf_path", None)
    request.session.pop("cached_pdf_name", None)
    if path and _is_safe_cache_path(path) and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            logger.warning("Gagal menghapus cache PDF: %s", path)


def _get_cached_pdf(request: HttpRequest):
    path = request.session.get("cached_pdf_path")
    name = request.session.get("cached_pdf_name")
    if path and _is_safe_cache_path(path) and os.path.exists(path):
        return path, name
    return None


def _save_cached_pdf(request: HttpRequest, pdf_file) -> str:
    _clear_cached_pdf(request)  # ganti PDF lama dengan yang baru
    os.makedirs(CACHE_DIR, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", dir=CACHE_DIR) as tmp:
        for chunk in pdf_file.chunks():
            tmp.write(chunk)
        path = tmp.name
    request.session["cached_pdf_path"] = path
    request.session["cached_pdf_name"] = pdf_file.name
    request.session.set_expiry(0)  # cookie sesi hilang saat browser ditutup
    return path


@require_POST
def cleanup_pdf_view(request: HttpRequest) -> HttpResponse:
    """Dipanggil via navigator.sendBeacon saat tab/browser ditutup."""
    _clear_cached_pdf(request)
    return HttpResponse(status=204)


def parse_float_input(val, default=0.0) -> float:
    if not val:
        return default
    try:
        cleaned = str(val).replace("Rp", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(cleaned)
    except Exception:
        return default

def parse_percent_input(val, default=0.0) -> float:
    if not val:
        return default
    try:
        cleaned = str(val).strip().replace("%", "").replace(" ", "")
        if "," in cleaned and "." not in cleaned:
            cleaned = cleaned.replace(",", ".")
        elif "," in cleaned and "." in cleaned:
            cleaned = cleaned.replace(",", "")
        return float(cleaned)
    except Exception:
        return default

def loan_calc_view(request: HttpRequest) -> HttpResponse:
    result = None
    error = None
    message = None

    _purge_expired_cache()

    # Membuka halaman baru (GET) / Clear Data = mulai bersih, PDF lama dihapus
    if request.method == "GET":
        _clear_cached_pdf(request)

    raw_exclude_banks = request.POST.get("excluded_banks", "") if request.method == "POST" else ""
    raw_exclude_branches = request.POST.get("excluded_branches", "") if request.method == "POST" else ""
    excluded_banks: List[str] = [b.strip() for b in raw_exclude_banks.split(",") if b.strip()]
    excluded_branches: List[str] = [c.strip() for c in raw_exclude_branches.split(",") if c.strip()]

    status_pekerjaan = request.POST.get("status_pekerjaan", "tetap") if request.method == "POST" else "tetap"
    penghasilan_bersih_raw = request.POST.get("penghasilan_bersih", "") if request.method == "POST" else ""
    harga_jual_raw = request.POST.get("harga_jual", "") if request.method == "POST" else ""
    uang_muka_raw = request.POST.get("uang_muka", "") if request.method == "POST" else ""
    suku_bunga_raw = request.POST.get("suku_bunga", "7.5") if request.method == "POST" else "7.5"
    bunga_floating_raw = request.POST.get("bunga_floating", "12.0") if request.method == "POST" else "12.0"
    tenor_tahun_raw = request.POST.get("tenor_tahun", "15") if request.method == "POST" else "15"
    persentase_custom_raw = request.POST.get("persentase_custom", "") if request.method == "POST" else ""

    penghasilan_bersih = parse_float_input(penghasilan_bersih_raw, 0.0)
    harga_jual = parse_float_input(harga_jual_raw, 0.0)
    uang_muka = parse_float_input(uang_muka_raw, 0.0)
    suku_bunga = parse_percent_input(suku_bunga_raw, 7.5)
    bunga_floating = parse_percent_input(bunga_floating_raw, 12.0)
    try:
        tenor_tahun = int(tenor_tahun_raw)
    except Exception:
        tenor_tahun = 15

    persentase_custom = parse_percent_input(persentase_custom_raw, 0.0)

    if request.method == "POST":
        pdf_file = request.FILES.get("pdf_file")
        pdf_path = None

        if pdf_file:
            # PDF baru diupload -> simpan (menggantikan cache sebelumnya)
            if not pdf_file.name.lower().endswith(".pdf"):
                error = "File harus berekstensi .pdf."
            else:
                try:
                    pdf_path = _save_cached_pdf(request, pdf_file)
                except Exception:
                    logger.exception("Gagal menyimpan PDF sementara")
                    error = "Gagal menyimpan file sementara di server."
        else:
            # Tidak ada upload baru -> pakai PDF sebelumnya jika masih ada
            cached = _get_cached_pdf(request)
            if cached:
                pdf_path = cached[0]
            else:
                error = "Silakan upload file PDF SLIK OJK."

        if pdf_path and not error:
            nama_nasabah = "TIDAK DITEMUKAN"
            try:
                nama_nasabah = extract_name(pdf_path)

                df, meta = extract_slik_data(
                    pdf_path=pdf_path,
                    excluded_banks=excluded_banks,
                    excluded_branches=excluded_branches,
                )

                if not meta.get("has_text", True):
                    message = "PDF tampaknya tidak berisi teks yang bisa diekstraksi (mungkin hasil scan tanpa OCR). Silakan cek ulang atau jalankan OCR terlebih dahulu."

                if df is None or (hasattr(df, "empty") and df.empty):
                    message = message or (
                        f"Nasabah atas nama {nama_nasabah} sudah lunas semua atau tidak ada "
                        "fasilitas yang valid."
                    )
                    result = {
                        "rows": [],
                        "total_monthly_payment": 0.0,
                        "nama": nama_nasabah,
                        "all_paid": True,
                        "problem_facilities": [],
                    }
                else:
                    try:
                        records = df.to_dict(orient="records")
                    except Exception:
                        logger.warning("extract_slik_data tidak mengembalikan DataFrame, mencoba cast ulang.")
                        import pandas as _pd
                        df = _pd.DataFrame(df) if not isinstance(df, _pd.DataFrame) else df
                        records = df.to_dict(orient="records")

                    aktif_records = [rec for rec in records if rec.get("kondisi") == "Aktif"]

                    rows = []
                    for i, rec in enumerate(aktif_records, start=1):
                        monthly_payment = rec.get("monthly_payment", 0.0) or 0.0
                        rows.append({
                            "no": i,
                            "bank": rec.get("bank", "TIDAK DITEMUKAN"),
                            "cabang": rec.get("cabang", "TIDAK DITEMUKAN"),
                            "loan_amount": rec.get("loan_amount", 0.0),
                            "interest_rate": rec.get("interest_rate", 0.0),
                            "loan_term_months": rec.get("loan_term_months", 0),
                            "start_date": rec.get("start_date"),
                            "end_date": rec.get("end_date"),
                            "monthly_payment": monthly_payment,
                            "total_payment": rec.get("total_payment", 0.0),
                            "total_interest": rec.get("total_interest", 0.0),
                            "annual_payment": rec.get("annual_payment", 0.0),
                            "mortgage_constant": rec.get("mortgage_constant", 0.0),
                            "page": rec.get("page", "N/A"),
                        })

                    problem_facilities = [
                        {
                            "pelapor": rec.get("bank", "TIDAK DITEMUKAN"),
                            "cabang": rec.get("cabang", "TIDAK DITEMUKAN"),
                            "tanggal_update": rec.get("tanggal_update", "TIDAK DITEMUKAN"),
                            "bulan_tahun": rec.get("bulan_tahun", "TIDAK DITEMUKAN"),
                            "kualitas": rec.get("kualitas", "TIDAK DITEMUKAN"),
                            "kondisi": rec.get("kondisi", "TIDAK DITEMUKAN"),
                        }
                        for rec in records
                        if is_kualitas_bermasalah(rec.get("kualitas", ""))
                    ]

                    first = records[0] if records else {}
                    nama_from_record = first.get("nama") or first.get("Nama")
                    final_name = nama_from_record if nama_from_record else nama_nasabah or "TIDAK DITEMUKAN"

                    all_paid = (len(aktif_records) == 0)
                    if all_paid:
                        message = message or (
                            f"Nasabah atas nama {final_name} sudah lunas semua atau tidak ada "
                            "fasilitas aktif yang valid."
                        )

                    result = {
                        "rows": rows,
                        "total_monthly_payment": meta.get("total_monthly_payment", 0.0),
                        "nama": final_name,
                        "all_paid": all_paid,
                        "problem_facilities": problem_facilities,
                    }

                if result is not None:
                    result["analisis_kredit"] = calculate_credit_analysis(
                        status_pekerjaan=status_pekerjaan,
                        penghasilan_bersih=penghasilan_bersih,
                        harga_jual=harga_jual,
                        uang_muka=uang_muka,
                        suku_bunga=suku_bunga,
                        bunga_floating=bunga_floating,
                        tenor_tahun=tenor_tahun,
                        total_monthly_slik=result.get("total_monthly_payment", 0.0),
                        problem_facilities_count=len(result.get("problem_facilities", [])),
                        persentase_custom=persentase_custom,
                    )
                    request.session["last_loan_result"] = serialize_result_for_export(result)

                if request.POST.get("export_csv") == "1" and result and result.get("rows"):
                    import io
                    import pandas as _pd

                    export_df = _pd.DataFrame(result["rows"])
                    csv_buffer = io.StringIO()
                    export_df.to_csv(csv_buffer, index=False)

                    safe_name = (result.get("nama") or "nasabah").replace(" ", "_")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"{safe_name}_loan_summary_{timestamp}.csv"

                    response = HttpResponse(csv_buffer.getvalue(), content_type="text/csv")
                    response["Content-Disposition"] = f'attachment; filename="{filename}"'
                    return response

            except Exception as e:
                logger.exception("Gagal memproses PDF SLIK OJK")
                error = f"Gagal memproses file: {str(e)}"
                nama_nasabah = extract_name(pdf_path) if pdf_path else "TIDAK DITEMUKAN"
                result = {
                    "rows": [],
                    "total_monthly_payment": 0.0,
                    "nama": nama_nasabah,
                    "all_paid": True,
                    "problem_facilities": [],
                    "analisis_kredit": calculate_credit_analysis(
                        status_pekerjaan=status_pekerjaan,
                        penghasilan_bersih=penghasilan_bersih,
                        harga_jual=harga_jual,
                        uang_muka=uang_muka,
                        suku_bunga=suku_bunga,
                        bunga_floating=bunga_floating,
                        tenor_tahun=tenor_tahun,
                        total_monthly_slik=0.0,
                        problem_facilities_count=0,
                        persentase_custom=persentase_custom,
                    ),
                }
    cached = _get_cached_pdf(request)

    context = {
        "result": result,
        "error": error,
        "message": message,
        "excluded_banks": raw_exclude_banks,
        "excluded_branches": raw_exclude_branches,
        "status_pekerjaan": status_pekerjaan,
        "penghasilan_bersih": penghasilan_bersih_raw,
        "harga_jual": harga_jual_raw,
        "uang_muka": uang_muka_raw,
        "suku_bunga": suku_bunga_raw,
        "bunga_floating": bunga_floating_raw,
        "tenor_tahun": tenor_tahun_raw,
        "persentase_custom": persentase_custom_raw,
        "cached_pdf_name": cached[1] if cached else "",
        "problem_facilities_json": json.dumps(
            result.get("problem_facilities", []) if result else [], default=str
        ),
    }

    return render(request, "loancalc/loan_form.html", context)

def export_pdf_view(request: HttpRequest) -> HttpResponse:
    from io import BytesIO
    from xhtml2pdf import pisa

    result = request.session.get("last_loan_result")
    if not result:
        return HttpResponse(
            "Tidak ada data untuk diexport. Silakan proses PDF SLIK terlebih dahulu.",
            status=400,
        )

    html_string = render_to_string("loancalc/loan_pdf.html", {
        "result": result,
        "generated_at": datetime.now(),
    })

    buffer = BytesIO()
    pisa_status = pisa.CreatePDF(html_string, dest=buffer)

    if pisa_status.err:
        return HttpResponse("Gagal membuat PDF.", status=500)

    safe_name = (result.get("nama") or "nasabah").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_name}_analisis_kredit_{timestamp}.pdf"

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response