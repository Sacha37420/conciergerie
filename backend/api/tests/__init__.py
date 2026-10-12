"""Tests backend de conciergerie.

    python manage.py test            (dans /app ; base PostgreSQL jetable, aucun réseau)

- test_serrure        : Verrou 2 — vrais jetons RS256 signés localement à travers
                        `KeycloakJWTAuthentication` (seul le JWKS est remplacé) ;
- test_autorisations  : gestionnaire (`admins`) / co-propriétaire (`proprietaires`),
                        cloisonnement par bien ;
- test_frais          : frais déclarés par un propriétaire, factures dans `storage` ;
- test_partages       : PartProprietaire ↔ ShareMember de `storage` (signaux) et
                        client HTTP de `storage` ;
- test_bilan          : grand livre de capital, recalculé à la main ;
- test_validations    : validations des sérialiseurs ;
- test_sync_airbnb    : synchronisation des calendriers iCal ;
- test_ical_destinations : l'URL iCal ne joint jamais une destination interne (DNS simulé).

Tout appel HTTP sortant (Keycloak, storage, Airbnb) est simulé ; un appel non
simulé lève une AssertionError au lieu d'attendre le réseau.
"""
