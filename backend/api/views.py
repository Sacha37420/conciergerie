from io import StringIO

from django.core.management import call_command
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Proprietaire, Entreprise, Bien, PartProprietaire, Appartement, Reservation
from .serializers import (
    ProprietaireSerializer, EntrepriseSerializer, BienSerializer,
    PartProprietaireSerializer, AppartementSerializer, ReservationSerializer,
)
from .permissions import IsManager, IsManagerOrOwner
from .scoping import is_manager, proprietaire_for, biens_du_proprietaire, reservations_du_proprietaire


class MeView(APIView):
    """GET /api/me/ — identité + rôle métier de l'utilisateur authentifié."""

    def get(self, request):
        groups = request.user.claims.get('groups', [])
        proprietaire = proprietaire_for(request.user)
        return Response({
            'email': request.user.email,
            'username': request.user.username,
            'groups': groups,
            'is_manager': is_manager(request),
            'proprietaire': ProprietaireSerializer(proprietaire).data if proprietaire else None,
        })


class ProprietaireViewSet(viewsets.ModelViewSet):
    """CRUD réservé au gestionnaire — un propriétaire ne s'auto-déclare pas."""
    queryset = Proprietaire.objects.all()
    serializer_class = ProprietaireSerializer
    permission_classes = [IsAuthenticated, IsManager]


class EntrepriseViewSet(viewsets.ModelViewSet):
    queryset = Entreprise.objects.all()
    serializer_class = EntrepriseSerializer
    permission_classes = [IsAuthenticated, IsManager]


class BienViewSet(viewsets.ModelViewSet):
    serializer_class = BienSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), IsManagerOrOwner()]
        return [IsAuthenticated(), IsManager()]

    def get_queryset(self):
        qs = Bien.objects.prefetch_related('parts__proprietaire', 'appartements')
        if not is_manager(self.request):
            qs = qs.filter(pk__in=biens_du_proprietaire(self.request.user))
        return qs


class PartProprietaireViewSet(viewsets.ModelViewSet):
    """Quote-parts — écriture réservée au gestionnaire (accord familial
    constaté, pas auto-déclaré). Un propriétaire peut lire les siennes."""
    serializer_class = PartProprietaireSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), IsManagerOrOwner()]
        return [IsAuthenticated(), IsManager()]

    def get_queryset(self):
        qs = PartProprietaire.objects.select_related('bien', 'proprietaire')
        if not is_manager(self.request):
            qs = qs.filter(bien__in=biens_du_proprietaire(self.request.user))
        bien_id = self.request.query_params.get('bien')
        if bien_id:
            qs = qs.filter(bien_id=bien_id)
        return qs


class AppartementViewSet(viewsets.ModelViewSet):
    serializer_class = AppartementSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), IsManagerOrOwner()]
        return [IsAuthenticated(), IsManager()]

    def get_queryset(self):
        qs = Appartement.objects.select_related('bien')
        if not is_manager(self.request):
            qs = qs.filter(bien__in=biens_du_proprietaire(self.request.user))
        bien_id = self.request.query_params.get('bien')
        if bien_id:
            qs = qs.filter(bien_id=bien_id)
        return qs


class ReservationViewSet(viewsets.ModelViewSet):
    """Manuel (`direct`/`autre`) créé/modifié par le gestionnaire ; les
    réservations `airbnb` sont en principe alimentées par le sync iCal mais
    restent modifiables (ex: renseigner `montant_revenu` à la main)."""
    serializer_class = ReservationSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), IsManagerOrOwner()]
        return [IsAuthenticated(), IsManager()]

    def get_queryset(self):
        qs = Reservation.objects.select_related('appartement__bien')
        if not is_manager(self.request):
            qs = reservations_du_proprietaire(self.request.user).select_related('appartement__bien')
        appartement_id = self.request.query_params.get('appartement')
        if appartement_id:
            qs = qs.filter(appartement_id=appartement_id)
        bien_id = self.request.query_params.get('bien')
        if bien_id:
            qs = qs.filter(appartement__bien_id=bien_id)
        return qs


class SyncAirbnbView(APIView):
    """POST /api/sync/airbnb/ — lance la synchro iCal de tous les
    appartements configurés, de façon synchrone (volume de données minime)."""
    permission_classes = [IsAuthenticated, IsManager]

    def post(self, request):
        out, err = StringIO(), StringIO()
        call_command('sync_airbnb_calendars', stdout=out, stderr=err)
        return Response({'log': out.getvalue(), 'erreurs': err.getvalue()})
