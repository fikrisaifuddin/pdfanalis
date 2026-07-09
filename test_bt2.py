import sys, os, logging
logging.disable(logging.CRITICAL)   # matikan debug log
sys.path.insert(0, ".")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "loan_project.settings")
import django
django.setup()

from loancalc.utils import extract_active_facilities, is_kualitas_bermasalah

pdfs = [
    "1102_CLU_29082024, arif setia.pdf",
    "551_CLU_13082024, zulkhifli pratama part 2.pdf",
    "980_CLU_26082024, masrozali.pdf",
]

for pdf in pdfs:
    print("")
    print("=== " + pdf + " ===")
    facilities = extract_active_facilities(pdf, [], [])
    if not facilities:
        print("  Tidak ada fasilitas aktif.")
    for i, fac in enumerate(facilities, 1):
        kualitas = str(fac.get("kualitas", ""))
        bulan = str(fac.get("bulan_tahun", ""))
        bank = str(fac.get("bank", ""))
        bermasalah = "*BERMASALAH*" if is_kualitas_bermasalah(kualitas) else ""
        print("  [" + str(i) + "] " + bermasalah)
        print("       Bank        : " + bank)
        print("       Kualitas    : " + kualitas)
        print("       Bulan/Tahun : " + bulan)
