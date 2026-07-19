"""Résout l'identité métier (Proprietaire) d'un utilisateur Keycloak et
scope les querysets en conséquence. Utilisé par toutes les vues portail."""
from .models import Bien, Proprietaire


def is_manager(request) -> bool:
    return 'admins' in getattr(request.user, 'claims', {}).get('groups', [])


def proprietaire_for(user) -> Proprietaire | None:
    if not getattr(user, 'email', ''):
        return None
    return Proprietaire.objects.filter(email__iexact=user.email).first()


def biens_du_proprietaire(user):
    return Bien.objects.filter(parts__proprietaire__email__iexact=user.email).distinct()


def reservations_du_proprietaire(user):
    from .models import Reservation
    return Reservation.objects.filter(appartement__bien__in=biens_du_proprietaire(user))
