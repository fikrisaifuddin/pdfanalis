from django import forms

DEFAULT_TEMPLATE = (
    "Halo {nama}, perkenalkan saya dari BTN. "
    "Ada info promo kredit yang mungkin cocok untuk Anda. "
    "Boleh saya kirimkan detailnya?"
)


class WaBlastUploadForm(forms.Form):
    excel_file = forms.FileField(
        label="File Excel (.xlsx)",
        help_text="Kolom wajib: Nama, No HP. Kolom lain akan diabaikan.",
        widget=forms.ClearableFileInput(attrs={"accept": ".xlsx,.xls"}),
    )
    template = forms.CharField(
        label="Template pesan",
        widget=forms.Textarea(attrs={"rows": 4}),
        initial=DEFAULT_TEMPLATE,
        help_text="Gunakan {nama} untuk otomatis diganti nama tiap kontak.",
    )