# loancalc_app/forms.py
import os
from django import forms
from django.conf import settings

def load_branch_choices():
    # Gunakan path absolut relatif terhadap BASE_DIR
    filepath = os.path.join(settings.BASE_DIR, "loancalc_app", "static", "btn_101_clean.txt")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
            return [(line, line) for line in lines if line.strip()]
    except FileNotFoundError:
        return []

class LoanExcludeForm(forms.Form):
    pdf_file = forms.FileField(label="Upload PDF SLIK OJK")

    excluded_branches = forms.MultipleChoiceField(
        label="Pilih Cabang/KCP/Kanwil yang Dikecualikan",
        choices=load_branch_choices(),
        widget=forms.SelectMultiple(attrs={
            'class': 'form-control select2',
            'style': 'width: 100%;'
        }),
        required=False
    )
