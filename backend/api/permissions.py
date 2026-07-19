from rest_framework.permissions import BasePermission


def _get_groups(request) -> list[str]:
    return getattr(request.user, 'claims', {}).get('groups', [])


class HasAnyRole(BasePermission):
    roles: list[str] = []

    def has_permission(self, request, view):
        return any(r in _get_groups(request) for r in self.roles)


class IsManager(HasAnyRole):
    """Gestionnaire — groupe `admins`. Accès complet."""
    roles = ['admins']


class IsOwner(HasAnyRole):
    """Propriétaire — groupe `proprietaires`. Accès scopé à ses biens."""
    roles = ['proprietaires']


class IsManagerOrOwner(HasAnyRole):
    roles = ['admins', 'proprietaires']
