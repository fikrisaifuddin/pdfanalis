from django import forms

DEFAULT_TEMPLATE = (
    "Selamat siang Hari {sapaan} {nama},\n"
    "Perkenalkan, saya Titan dari Bank BTN.\n"
    "Saya ingin menyampaikan kabar baik bahwa karena riwayat pembayaran KPR {sapaan} yang sangat lancar, "
    "{sapaan} berkesempatan mendapatkan benefit khusus berupa penurunan suku bunga KPR.\n"
    "Selain itu, apabila {sapaan} memiliki kebutuhan dana tambahan, saat ini Bank BTN juga memiliki "
    "Program Kredit Agunan Rumah (KAR) dengan Suku bunga promo mulai 5,99% fixed 1 tahun, sehingga "
    "{sapaan} dapat memanfaatkan nilai aset rumah untuk berbagai kebutuhan, seperti renovasi rumah, "
    "biaya pendidikan, modal usaha, maupun kebutuhan lainnya.\n"
    "Apabila {sapaan} berkenan mengetahui detail mengenai penyesuaian suku bunga KPR maupun Program KAR ini, "
    "saya dengan senang hati siap membantu memberikan informasi lebih lanjut.\n"
    "Terima kasih atas kepercayaan {sapaan} kepada Bank BTN. Semoga {sapaan} selalu sehat dan sukses. \U0001F64F\U0001F3FB"
)


class WaBlastUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="File Excel (.xlsx)",
        help_text="Kolom wajib: Nama, No HP. Kolom lain akan diabaikan.",
        widget=forms.ClearableFileInput(attrs={
            "accept": ".xlsx,.xls",
            "class": "form-control form-control-sm",
        }),
    )
    template = forms.CharField(
        label="Template pesan",
        widget=forms.Textarea(attrs={
            "rows": 8,
            "class": "form-control",
            "style": "resize: vertical; font-size: 0.88rem;",
        }),
        initial=DEFAULT_TEMPLATE,
        help_text="Gunakan {nama} untuk nama kontak, dan {sapaan} untuk Bapak/Ibu (diatur per kontak di tabel).",
    )
    