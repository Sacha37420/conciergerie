from io import StringIO

from django.core.management import call_command
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import (
    Proprietaire, Entreprise, Bien, PartProprietaire, Appartement, Reservation,
    Tache, Frais,
)
from .serializers import (
    ProprietaireSerializer, EntrepriseSerializer, BienSerializer,
    PartProprietaireSerializer, AppartementSerializer, ReservationSerializer,
    TacheSerializer, FraisSerializer,
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


class TacheViewSet(viewsets.ModelViewSet):
    """Écriture (création/édition/suppression de tâches) réservée au
    gestionnaire. Un propriétaire voit toutes les tâches de ses biens, quel
    que soit le responsable (transparence de co-propriété)."""
    serializer_class = TacheSerializer

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [IsAuthenticated(), IsManagerOrOwner()]
        return [IsAuthenticated(), IsManager()]

    def get_queryset(self):
        qs = Tache.objects.select_related(
            'bien', 'appartement', 'proprietaire_responsable', 'entreprise_responsable',
        ).prefetch_related('frais')
        if not is_manager(self.request):
            qs = qs.filter(bien__in=biens_du_proprietaire(self.request.user))
        bien_id = self.request.query_params.get('bien')
        if bien_id:
            qs = qs.filter(bien_id=bien_id)
        appartement_id = self.request.query_params.get('appartement')
        if appartement_id:
            qs = qs.filter(appartement_id=appartement_id)
        statut = self.request.query_params.get('statut')
        if statut:
            qs = qs.filter(statut=statut)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user.email)


class FraisViewSet(viewsets.ModelViewSet):
    """Le gestionnaire a tous les droits. Un propriétaire peut UNIQUEMENT
    déclarer un frais qu'il a payé lui-même (`payeur=proprietaire`,
    `proprietaire_payeur` = sa propre fiche), sur un bien qu'il possède,
    avec la facture jointe — jamais un frais payé par la maison, ni pour un
    autre propriétaire. Lecture : transparence totale sur ses biens."""
    serializer_class = FraisSerializer

    def get_permissions(self):
        return [IsAuthenticated(), IsManagerOrOwner()]

    def get_queryset(self):
        qs = Frais.objects.select_related('tache__bien', 'proprietaire_payeur')
        if not is_manager(self.request):
            qs = qs.filter(tache__bien__in=biens_du_proprietaire(self.request.user))
        tache_id = self.request.query_params.get('tache')
        if tache_id:
            qs = qs.filter(tache_id=tache_id)
        return qs

    def perform_create(self, serializer):
        if is_manager(self.request):
            serializer.save(created_by=self.request.user.email)
            return

        proprietaire = proprietaire_for(self.request.user)
        tache = serializer.validated_data.get('tache')
        if not proprietaire or tache is None or tache.bien not in biens_du_proprietaire(self.request.user):
            raise PermissionDenied('Vous ne pouvez déclarer un frais que sur un bien que vous possédez.')
        if (
            serializer.validated_data.get('payeur') != 'proprietaire'
            or serializer.validated_data.get('proprietaire_payeur') != proprietaire
        ):
            raise PermissionDenied('Vous ne pouvez déclarer que des frais que vous avez payés vous-même.')
        serializer.save(created_by=self.request.user.email)

    def perform_update(self, serializer):
        instance = serializer.instance
        if not is_manager(self.request):
            proprietaire = proprietaire_for(self.request.user)
            if instance.proprietaire_payeur_id != getattr(proprietaire, 'id', None) or instance.est_rembourse:
                raise PermissionDenied(
                    'Vous ne pouvez modifier que vos propres frais non encore remboursés.'
                )
        serializer.save()

    def perform_destroy(self, instance):
        if not is_manager(self.request):
            proprietaire = proprietaire_for(self.request.user)
            if instance.proprietaire_payeur_id != getattr(proprietaire, 'id', None) or instance.est_rembourse:
                raise PermissionDenied(
                    'Vous ne pouvez supprimer que vos propres frais non encore remboursés.'
                )
        instance.delete()


class SyncAirbnbView(APIView):
    """POST /api/sync/airbnb/ — lance la synchro iCal de tous les
    appartements configurés, de façon synchrone (volume de données minime)."""
    permission_classes = [IsAuthenticated, IsManager]

    def post(self, request):
        out, err = StringIO(), StringIO()
        call_command('sync_airbnb_calendars', stdout=out, stderr=err)
        return Response({'log': out.getvalue(), 'erreurs': err.getvalue()})
