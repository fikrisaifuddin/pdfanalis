from django.db import models

class BuktiSurvei(models.Model):
    foto = models.ImageField(upload_to='bukti_survei/%Y/%m/%d/')
    keterangan = models.CharField(max_length=255, blank=True)
    diambil_pada = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-diambil_pada']

    def __str__(self):
        return f"{self.keterangan or 'Bukti Survei'} - {self.diambil_pada:%d %b %Y %H:%M}"