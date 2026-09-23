from django.urls import path
from . import views

urlpatterns = [
    path("", views.loan_calc_view, name="loan_calc"),
    path("export-pdf/", views.export_pdf_view, name="export_pdf"),
]