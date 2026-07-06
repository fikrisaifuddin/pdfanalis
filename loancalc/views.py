import os
import tempfile
import logging
from typing import List
from datetime import datetime

from django.shortcuts import render
from django.http import HttpRequest, HttpResponse

from .utils import extract_slik_data, extract_name

logger = logging.getLogger(__name__)


def loan_calc_view(request: HttpRequest) -> HttpResponse:
    result = None
    error = None
    message = None

    raw_exclude_banks = request.POST.get("excluded_banks", "") if request.method == "POST" else ""
    raw_exclude_branches = request.POST.get("excluded_branches", "") if request.method == "POST" else ""
    excluded_banks: List[str] = [b.strip() for b in raw_exclude_banks.split(",") if b.strip()]
    excluded_branches: List[str] = [c.strip() for c in raw_exclude_branches.split(",") if c.strip()]

    # Default for GET
    if request.method != "POST":
        result = {
            "rows": [],
            "total_monthly_payment": 0.0,
            "nama": "TIDAK DITEMUKAN",
            "all_paid": True,
        }

    if request.method == "POST":
        pdf_file = request.FILES.get("pdf_file")
        if not pdf_file:
            error = "Silakan upload file PDF SLIK OJK."
        else:
            if not pdf_file.name.lower().endswith(".pdf"):
                error = "File harus berekstensi .pdf."
            else:
                tmp_path = None
                nama_nasabah = "TIDAK DITEMUKAN"
                try:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                        for chunk in pdf_file.chunks():
                            tmp.write(chunk)
                        tmp_path = tmp.name

                    # Ambil nama nasabah awal (fallback)
                    nama_nasabah = extract_name(tmp_path)

                    df, meta = extract_slik_data(
                        pdf_path=tmp_path,
                        excluded_banks=excluded_banks,
                        excluded_branches=excluded_branches,
                    )

                    # tidak ada teks sama sekali (misal: scan tanpa OCR)
                    if not meta.get("has_text", True):
                        message = "PDF tampaknya tidak berisi teks yang bisa diekstraksi (mungkin hasil scan tanpa OCR). Silakan cek ulang atau jalankan OCR terlebih dahulu."
                    
                    if df is None or (hasattr(df, "empty") and df.empty) or meta.get("facility_count", 0) == 0:
                        # semua lunas / tidak ada fasilitas aktif valid
                        message = message or (
                            f"Nasabah atas nama {nama_nasabah} sudah lunas semua atau tidak ada "
                            "fasilitas aktif yang valid."
                        )
                        result = {
                            "rows": [],
                            "total_monthly_payment": 0.0,
                            "nama": nama_nasabah,
                            "all_paid": True,
                        }
                    else:
                        # Pastikan DataFrame
                        try:
                            records = df.to_dict(orient="records")
                        except Exception:
                            logger.warning("extract_slik_data tidak mengembalikan DataFrame, mencoba cast ulang.")
                            import pandas as _pd
                            df = _pd.DataFrame(df) if not isinstance(df, _pd.DataFrame) else df
                            records = df.to_dict(orient="records")

                        rows = []
                        for rec in records:
                            monthly_payment = rec.get("monthly_payment", 0.0) or 0.0
                            rows.append({
                                "no": rec.get("no", ""),
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

                        first = records[0] if records else {}
                        nama_from_record = first.get("nama") or first.get("Nama")
                        final_name = nama_from_record if nama_from_record else nama_nasabah or "TIDAK DITEMUKAN"

                        result = {
                            "rows": rows,
                            "total_monthly_payment": meta.get("total_monthly_payment", 0.0),
                            "nama": final_name,
                            "all_paid": False,
                        }

                    # Export CSV jika diminta
                    if request.POST.get("export_csv") == "1" and result and result.get("rows"):
                        import io
                        import pandas as _pd

                        export_df = _pd.DataFrame(result["rows"])
                        csv_buffer = io.StringIO()
                        export_df.to_csv(csv_buffer, index=False)
                        csv_content = csv_buffer.getvalue()

                        safe_name = (result.get("nama") or "nasabah").replace(" ", "_")
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"{safe_name}_loan_summary_{timestamp}.csv"

                        response = HttpResponse(csv_content, content_type="text/csv")
                        response["Content-Disposition"] = f'attachment; filename="{filename}"'
                        return response

                except Exception as e:
                    logger.exception("Gagal memproses PDF SLIK OJK")
                    error = f"Gagal memproses file: {str(e)}"
                    nama_nasabah = extract_name(tmp_path) if tmp_path else "TIDAK DITEMUKAN"
                    result = {
                        "rows": [],
                        "total_monthly_payment": 0.0,
                        "nama": nama_nasabah,
                        "all_paid": True,
                    }
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            logger.warning("Gagal menghapus file temporer: %s", tmp_path)

    # Pastikan result tidak None
    if result is None:
        result = {
            "rows": [],
            "total_monthly_payment": 0.0,
            "nama": "TIDAK DITEMUKAN",
            "all_paid": True,
        }

    context = {
        "result": result,
        "error": error,
        "message": message,
        "excluded_banks": raw_exclude_banks,
        "excluded_branches": raw_exclude_branches,
    }
    return render(request, "loancalc/loan_form.html", context)