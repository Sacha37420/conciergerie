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


def proprietaires_visibles(request) -> set[int]:
    """Identifiants des propriétaires dont les contacts (nom, email,
    téléphone) peuvent être rendus à l'appelant non gestionnaire : lui-même et
    les co-propriétaires d'un bien où il a lui-même une part — jamais ceux d'un
    autre bien (décision du propriétaire, chantier « failles », 2026-10-31).
    Calculé une fois par requête."""
    cache = getattr(request, '_conciergerie_proprietaires_visibles', None)
    if cache is not None:
        return cache
    from .models import PartProprietaire
    moi = proprietaire_for(request.user)
    visibles = set()
    if moi is not None:
        visibles.add(moi.id)
        visibles.update(
            PartProprietaire.objects
            .filter(bien__parts__proprietaire=moi)
            .values_list('proprietaire_id', flat=True)
        )
    request._conciergerie_proprietaires_visibles = visibles
    return visibles
