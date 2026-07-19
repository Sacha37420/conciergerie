import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KeycloakService } from '../../core/keycloak.service';
import { ApiService, Bien, Appartement, Reservation } from '../../core/api.service';

@Component({
  selector: 'app-reservations',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './reservations.component.html',
  styleUrl: './reservations.component.scss',
})
export class ReservationsComponent implements OnInit {
  private api = inject(ApiService);
  protected kc = inject(KeycloakService);

  biens = signal<Bien[]>([]);
  reservations = signal<Reservation[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  syncing = signal(false);
  syncLog = signal<string | null>(null);

  filterBienId: number | '' = '';

  showModal = signal(false);
  editing = signal<Reservation | null>(null);
  form: Partial<Reservation> = {};

  get isManager(): boolean { return this.kc.isManager; }

  get appartements(): Appartement[] {
    return this.biens().flatMap(b => b.appartements ?? []);
  }

  ngOnInit(): void {
    this.api.getBiens().subscribe({ next: b => this.biens.set(b) });
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getReservations(this.filterBienId || undefined).subscribe({
      next: items => { this.reservations.set(items); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger les réservations.'); this.loading.set(false); },
    });
  }

  nomAppartement(id: number): string {
    const a = this.appartements.find(x => x.id === id);
    if (!a) return '—';
    const bien = this.biens().find(b => b.id === a.bien);
    return bien ? `${bien.nom} — ${a.nom}` : a.nom;
  }

  sync(): void {
    this.syncing.set(true);
    this.syncLog.set(null);
    this.api.syncAirbnb().subscribe({
      next: res => { this.syncing.set(false); this.syncLog.set(res.log || 'Synchronisation terminée.'); this.load(); },
      error: () => { this.syncing.set(false); this.error.set('Échec de la synchronisation Airbnb.'); },
    });
  }

  openCreate(): void {
    this.form = {
      appartement: this.appartements[0]?.id, source: 'direct', statut: 'confirmee',
      date_debut: '', date_fin: '', libelle: '', montant_revenu: null, notes: '',
    };
    this.editing.set(null);
    this.showModal.set(true);
  }

  openEdit(item: Reservation): void {
    this.form = { ...item };
    this.editing.set(item);
    this.showModal.set(true);
  }

  save(): void {
    const id = this.editing()?.id;
    const obs = id
      ? this.api.updateReservation(id, this.form)
      : this.api.createReservation(this.form as Reservation);
    obs.subscribe({
      next: () => { this.showModal.set(false); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement."),
    });
  }

  delete(item: Reservation): void {
    if (!confirm('Supprimer cette réservation ?')) return;
    this.api.deleteReservation(item.id!).subscribe({ next: () => this.load() });
  }

  close(): void { this.showModal.set(false); }
}
