"""Synchronise les réservations Airbnb depuis les flux iCal des appartements.

Lecture seule : Airbnb n'expose via iCal que les créneaux occupés (SUMMARY +
dates), jamais le nom du voyageur ni le montant du séjour — cf. api/bilan.py
et le champ Reservation.montant_revenu (saisie manuelle).

Lancée en tâche de fond toutes les 30 min par le conteneur backend (voir
docker-compose.yml) et à la demande via POST /api/sync/airbnb/.
"""
import datetime

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from icalendar import Calendar

from api.models import Appartement, Reservation


def _to_date(value) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()
    return value


class Command(BaseCommand):
    help = "Synchronise les réservations Airbnb (iCal) de tous les appartements."

    def handle(self, *args, **options):
        appartements = Appartement.objects.exclude(airbnb_ical_url='')
        if not appartements:
            self.stdout.write('Aucun appartement avec URL iCal configurée.')
            return
        for appartement in appartements:
            self._sync_appartement(appartement)

    def _sync_appartement(self, appartement: Appartement) -> None:
        try:
            resp = requests.get(appartement.airbnb_ical_url, timeout=15)
            resp.raise_for_status()
            cal = Calendar.from_ical(resp.content)
        except Exception as exc:
            appartement.dernier_sync_erreur = str(exc)[:2000]
            appartement.dernier_sync_at = timezone.now()
            appartement.save(update_fields=['dernier_sync_erreur', 'dernier_sync_at'])
            self.stderr.write(f'[{appartement}] échec sync : {exc}')
            return

        uids_vus = set()
        for event in cal.walk('VEVENT'):
            uid = str(event.get('UID', '')).strip()
            if not uid:
                continue
            uids_vus.add(uid)

            dtstart_prop = event.get('DTSTART')
            dtend_prop = event.get('DTEND')
            if dtstart_prop is None:
                continue
            date_debut = _to_date(dtstart_prop.dt)
            date_fin = _to_date(dtend_prop.dt) if dtend_prop is not None else date_debut
            statut = 'annulee' if str(event.get('STATUS', '')).upper() == 'CANCELLED' else 'confirmee'
            libelle = str(event.get('SUMMARY', ''))

            Reservation.objects.update_or_create(
                appartement=appartement, source='airbnb', uid_externe=uid,
                defaults={
                    'date_debut': date_debut,
                    'date_fin': date_fin,
                    'libelle': libelle,
                    'statut': statut,
                },
            )

        # Une réservation Airbnb future absente du flux a été annulée/déplacée
        # côté Airbnb sans laisser de VEVENT STATUS:CANCELLED — on l'aligne.
        today = timezone.localdate()
        (
            Reservation.objects
            .filter(appartement=appartement, source='airbnb', statut='confirmee', date_fin__gte=today)
            .exclude(uid_externe__in=uids_vus)
            .update(statut='annulee')
        )

        appartement.dernier_sync_at = timezone.now()
        appartement.dernier_sync_erreur = ''
        appartement.save(update_fields=['dernier_sync_at', 'dernier_sync_erreur'])
        self.stdout.write(f'[{appartement}] sync OK — {len(uids_vus)} événement(s)')
