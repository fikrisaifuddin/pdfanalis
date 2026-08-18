from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_POST

from .models import BuktiSurvei
from .forms import BuktiSurveiForm


def index(request):
    if request.method == 'POST':
        form = BuktiSurveiForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Bukti survei berhasil diunggah.')
            return redirect('buktisurvei:index')
    else:
        form = BuktiSurveiForm()

    daftar = BuktiSurvei.objects.all()
    return render(request, 'buktisurvei/list.html', {
        'form': form,
        'daftar': daftar,
    })


@require_POST
def hapus(request, pk):
    foto = get_object_or_404(BuktiSurvei, pk=pk)
    foto.foto.delete(save=False)   # hapus file fisiknya juga dari folder media
    foto.delete()                  # hapus record di database
    messages.success(request, 'Foto berhasil dihapus.')
    return redirect('buktisurvei:index')