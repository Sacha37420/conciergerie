"""Backfill à usage unique : synchronise les `PartProprietaire` déjà en base
(créées avant le câblage des signaux post_save/post_delete, cf. api/signals.py)
avec de vrais ShareMember sur le partage storage de chaque bien.

Idempotent (storage_client.add_member fait un get_or_create côté rôle) —
peut être relancé sans risque, y compris après le déploiement qui a introduit
les signaux (les parts créées depuis ce déploiement sont déjà synchronisées
par le signal, ce backfill est un no-op pour elles).
"""
from django.core.management.base import BaseCommand

from api import storage_client
from api.models import PartProprietaire


class Command(BaseCommand):
    help = "Synchronise les PartProprietaire existantes vers de vrais ShareMember storage."

    def handle(self, *args, **options):
        parts = PartProprietaire.objects.select_related('bien', 'proprietaire').all()
        if not parts:
            self.stdout.write('Aucune PartProprietaire en base.')
            return
        ok, skipped, failed = 0, 0, 0
        for part in parts:
            email = part.proprietaire.email
            if not email:
                self.stdout.write(
                    f'  - ignoré : {part.proprietaire.nom} (bien #{part.bien_id}) sans email.'
                )
                skipped += 1
                continue
            try:
                storage_client.add_member(part.bien_id, email, role='read')
            except storage_client.StorageClientError as exc:
                self.stderr.write(f'  ! échec : {part.proprietaire.nom} (bien #{part.bien_id}) : {exc}')
                failed += 1
                continue
            self.stdout.write(f'  - ok : {email} → conciergerie-bien-{part.bien_id}')
            ok += 1
        self.stdout.write(self.style.SUCCESS(f'{ok} synchronisés, {skipped} ignorés, {failed} en échec.'))
