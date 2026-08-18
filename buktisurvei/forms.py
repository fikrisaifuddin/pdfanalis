from django import forms
from .models import BuktiSurvei

class BuktiSurveiForm(forms.ModelForm):
    class Meta:
        model = BuktiSurvei
        fields = ['foto', 'keterangan']
        widgets = {
            'foto': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
                'capture': 'environment',
            }),
            'keterangan': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Keterangan (opsional)',
            }),
        }