from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MeView, ProprietaireViewSet, EntrepriseViewSet, BienViewSet,
    PartProprietaireViewSet, AppartementViewSet, ReservationViewSet, SyncAirbnbView,
    TacheViewSet, FraisViewSet, RemboursementViewSet, ApportInitialViewSet,
    VersementRevenuViewSet,
)

router = DefaultRouter()
router.register('proprietaires', ProprietaireViewSet)
router.register('entreprises', EntrepriseViewSet)
router.register('biens', BienViewSet, basename='bien')
router.register('parts-proprietaire', PartProprietaireViewSet, basename='part-proprietaire')
router.register('appartements', AppartementViewSet, basename='appartement')
router.register('reservations', ReservationViewSet, basename='reservation')
router.register('taches', TacheViewSet, basename='tache')
router.register('frais', FraisViewSet, basename='frais')
router.register('remboursements', RemboursementViewSet, basename='remboursement')
router.register('apports-initiaux', ApportInitialViewSet, basename='apport-initial')
router.register('versements-revenu', VersementRevenuViewSet, basename='versement-revenu')

urlpatterns = [
    path('me/', MeView.as_view()),
    path('sync/airbnb/', SyncAirbnbView.as_view()),
    path('', include(router.urls)),
]
