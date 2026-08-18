from django.urls import path
from . import views

app_name = 'buktisurvei'

urlpatterns = [
    path('', views.index, name='index'),
    path('hapus/<int:pk>/', views.hapus, name='hapus'),
]