from django.urls import path
from .views import loan_calc_view

urlpatterns = [
    path('', loan_calc_view, name='loan_calc'),
]
