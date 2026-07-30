"""Synchronise le modèle de partage propre à conciergerie (co-propriétaires
d'un bien, ``PartProprietaire``) avec de vrais ``ShareMember`` sur le
partage storage du bien (``conciergerie-bien-<id>``) — voir
``storage_client.add_member``/``remove_member``.

Pourquoi des **signaux** plutôt qu'un simple override de
``PartProprietaireViewSet.perform_create``/``perform_destroy`` : une
``PartProprietaire`` disparaît aussi par cascade (suppression d'un ``Bien``
ou d'un ``Proprietaire``), un chemin qui ne passe par aucune vue — même
raisonnement que la purge de fichiers de ``arbre-genealogique`` (cf.
``dev/CLAUDE.md``, section « Fichiers rasters / médias »).

Ces appels storage sont volontairement **non best-effort** : une erreur
(``StorageClientError``, sous-classe de ``RuntimeError``) remonte et fait
échouer l'opération DB qui l'a déclenchée — un échec doit être visible (500
côté API) plutôt qu'avalé, pour ne jamais laisser un ``ShareMember`` dériver
silencieusement du modèle applicatif (accès orphelin si un retrait échoue,
accès manquant si un ajout échoue). Ce choix diffère délibérément de la
purge best-effort d'``arbre-genealogique`` : là-bas la donnée DB était déjà
validée et il ne s'agissait que d'éviter des blobs orphelins ; ici c'est
l'inverse.

Deux mécanismes distincts garantissent que l'échec fait bien *rollback* la
modification DB déclenchante (vérifié en réel, cf. rapport de migration) :

- **Suppression** (``post_delete``) : le ``Collector`` de Django encapsule
  nativement toute suppression (y compris par cascade — Bien ou Proprietaire
  supprimé) dans une transaction, avec ou sans ``ATOMIC_REQUESTS``. Rien à
  ajouter côté vue.
- **Création/modification** (``post_save``) : un ``Model.save()`` isolé
  n'est PAS encapsulé par défaut (vérifié : un ``post_save`` qui lève laisse
  la ligne déjà committée). ``PartProprietaireViewSet.perform_create``/
  ``perform_update`` et ``ProprietaireViewSet.perform_update`` (api/views.py)
  enveloppent donc explicitement ``serializer.save()`` dans
  ``transaction.atomic()`` pour obtenir la même garantie.
"""
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from . import storage_client
from .models import PartProprietaire, Proprietaire


@receiver(pre_save, sender=PartProprietaire)
def _capture_previous_part(sender, instance, **kwargs):
    """Mémorise (bien, proprietaire, email) avant écriture, pour que le
    post_save puisse détecter un changement sur une part existante (PATCH/PUT
    — exposé par l'API même si l'UI actuelle ne fait que créer/supprimer,
    cf. ApiService.updatePartProprietaire)."""
    if instance.pk is None:
        instance._previous_part = None
        return
    try:
        previous = PartProprietaire.objects.select_related('proprietaire').get(pk=instance.pk)
    except PartProprietaire.DoesNotExist:
        instance._previous_part = None
        return
    instance._previous_part = (previous.bien_id, previous.proprietaire_id, previous.proprietaire.email)


@receiver(post_save, sender=PartProprietaire)
def _sync_share_member_on_save(sender, instance, created, **kwargs):
    new_email = instance.proprietaire.email
    if created:
        if new_email:
            storage_client.add_member(instance.bien_id, new_email, role='read')
        return

    previous = getattr(instance, '_previous_part', None)
    if previous is None:
        return
    old_bien_id, old_proprietaire_id, old_email = previous
    if old_bien_id == instance.bien_id and old_proprietaire_id == instance.proprietaire_id:
        return  # rien de pertinent au partage n'a changé
    if old_email:
        storage_client.remove_member(old_bien_id, old_email)
    if new_email:
        storage_client.add_member(instance.bien_id, new_email, role='read')


@receiver(post_delete, sender=PartProprietaire)
def _revoke_share_member(sender, instance, **kwargs):
    email = instance.proprietaire.email
    if email:
        storage_client.remove_member(instance.bien_id, email)


@receiver(pre_save, sender=Proprietaire)
def _resync_on_email_change(sender, instance, **kwargs):
    """Un propriétaire change d'email (typo corrigée, nouvelle adresse) →
    migre ses ShareMember sur chacun de ses biens, sinon l'ancienne adresse
    garde un accès storage résiduel et la nouvelle n'en a aucun."""
    if instance.pk is None:
        return  # création — aucune part encore rattachée
    previous_email = Proprietaire.objects.filter(pk=instance.pk).values_list('email', flat=True).first()
    if previous_email == instance.email:
        return
    bien_ids = list(instance.parts.values_list('bien_id', flat=True))
    for bien_id in bien_ids:
        if previous_email:
            storage_client.remove_member(bien_id, previous_email)
        if instance.email:
            storage_client.add_member(bien_id, instance.email, role='read')
