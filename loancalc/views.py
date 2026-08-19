import os
import tempfile
import logging
from typing import List
from datetime import datetime
import json

from django.shortcuts import render
from django.http import HttpRequest, HttpResponse

from .utils import extract_slik_data, extract_name, is_kualitas_bermasalah, calculate_credit_analysis

logger = logging.getLogger(__name__)


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
    problem_facilities_json = "[]"

    raw_exclude_banks = request.POST.get("excluded_banks", "") if request.method == "POST" else ""
    raw_exclude_branches = request.POST.get("excluded_branches", "") if request.method == "POST" else ""
    excluded_banks: List[str] = [b.strip() for b in raw_exclude_banks.split(",") if b.strip()]
    excluded_branches: List[str] = [c.strip() for c in raw_exclude_branches.split(",") if c.strip()]

    # Parameter Analisis Kredit / KPR
    status_pekerjaan = request.POST.get("status_pekerjaan", "tetap") if request.method == "POST" else "tetap"
    penghasilan_bersih_raw = request.POST.get("penghasilan_bersih", "") if request.method == "POST" else ""
    harga_jual_raw = request.POST.get("harga_jual", "") if request.method == "POST" else ""
    uang_muka_raw = request.POST.get("uang_muka", "") if request.method == "POST" else ""
    suku_bunga_raw = request.POST.get("suku_bunga", "7.5") if request.method == "POST" else "7.5"
    bunga_floating_raw = request.POST.get("bunga_floating", "12.0") if request.method == "POST" else "12.0"
    tenor_tahun_raw = request.POST.get("tenor_tahun", "15") if request.method == "POST" else "15"

    penghasilan_bersih = parse_float_input(penghasilan_bersih_raw, 0.0)
    harga_jual = parse_float_input(harga_jual_raw, 0.0)
    uang_muka = parse_float_input(uang_muka_raw, 0.0)
    suku_bunga = parse_percent_input(suku_bunga_raw, 7.5)
    bunga_floating = parse_percent_input(bunga_floating_raw, 12.0)
    try:
        tenor_tahun = int(tenor_tahun_raw)
    except Exception:
        tenor_tahun = 15

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

                    if df is None or (hasattr(df, "empty") and df.empty):
                        # PDF benar-benar tidak ada fasilitas yang terdeteksi sama sekali
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
                        # Pastikan DataFrame
                        try:
                            records = df.to_dict(orient="records")
                        except Exception:
                            logger.warning("extract_slik_data tidak mengembalikan DataFrame, mencoba cast ulang.")
                            import pandas as _pd
                            df = _pd.DataFrame(df) if not isinstance(df, _pd.DataFrame) else df
                            records = df.to_dict(orient="records")

                        # Hanya fasilitas berkondisi Aktif yang masuk ke tabel utama
                        # & dihitung sebagai hutang berjalan nasabah.
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

                        # Popup "Kualitas Bermasalah" mengambil dari SEMUA fasilitas
                        # (Aktif maupun Lunas) yang riwayat kualitasnya bermasalah.
                        # Ini supaya nasabah yang dulu pernah menunggak tapi
                        # sekarang sudah Lunas tetap kelihatan statusnya, bukan
                        # ikut hilang bersama fasilitas Lunas lain.
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

                        problem_facilities_json = json.dumps(problem_facilities, default=str)

                        first = records[0] if records else {}
                        nama_from_record = first.get("nama") or first.get("Nama")
                        final_name = nama_from_record if nama_from_record else nama_nasabah or "TIDAK DITEMUKAN"

                        # "all_paid" sekarang murni berdasarkan ada/tidaknya fasilitas
                        # Aktif — meski begitu, problem_facilities (termasuk yang
                        # sudah Lunas) tetap disertakan supaya popup tetap muncul.
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

                    # Hitung analisis kredit (3 aspek)
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
                        )

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
                        ),
                    }
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            logger.warning("Gagal menghapus file temporer: %s", tmp_path)

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
        "problem_facilities_json": json.dumps(
            result.get("problem_facilities", []) if result else [], default=str
        ),
    }

    return render(request, "loancalc/loan_form.html", context)