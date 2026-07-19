from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MeView, ProprietaireViewSet, EntrepriseViewSet, BienViewSet,
    PartProprietaireViewSet, AppartementViewSet, ReservationViewSet, SyncAirbnbView,
)

router = DefaultRouter()
router.register('proprietaires', ProprietaireViewSet)
router.register('entreprises', EntrepriseViewSet)
router.register('biens', BienViewSet, basename='bien')
router.register('parts-proprietaire', PartProprietaireViewSet, basename='part-proprietaire')
router.register('appartements', AppartementViewSet, basename='appartement')
router.register('reservations', ReservationViewSet, basename='reservation')

urlpatterns = [
    path('me/', MeView.as_view()),
    path('sync/airbnb/', SyncAirbnbView.as_view()),
    path('', include(router.urls)),
]
