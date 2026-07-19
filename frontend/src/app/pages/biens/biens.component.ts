import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KeycloakService } from '../../core/keycloak.service';
import {
  ApiService, Bien, Proprietaire, PartProprietaire, Appartement,
} from '../../core/api.service';

@Component({
  selector: 'app-biens',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './biens.component.html',
  styleUrl: './biens.component.scss',
})
export class BiensComponent implements OnInit {
  private api = inject(ApiService);
  protected kc = inject(KeycloakService);

  biens = signal<Bien[]>([]);
  proprietaires = signal<Proprietaire[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  expandedId = signal<number | null>(null);

  showBienModal = signal(false);
  editingBien = signal<Bien | null>(null);
  bienForm: Bien = { nom: '' };

  showPartModal = signal(false);
  editingPart = signal<PartProprietaire | null>(null);
  partForm: Partial<PartProprietaire> = {};
  partBienId: number | null = null;

  showAppartModal = signal(false);
  editingAppart = signal<Appartement | null>(null);
  appartForm: Partial<Appartement> = {};
  appartBienId: number | null = null;

  protected readonly Number = Number;

  get isManager(): boolean { return this.kc.isManager; }

  ngOnInit(): void {
    this.load();
    if (this.isManager) {
      this.api.getProprietaires().subscribe({ next: p => this.proprietaires.set(p) });
    }
  }

  load(): void {
    this.loading.set(true);
    this.api.getBiens().subscribe({
      next: items => { this.biens.set(items); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger les biens.'); this.loading.set(false); },
    });
  }

  toggleExpand(bien: Bien): void {
    this.expandedId.set(this.expandedId() === bien.id ? null : bien.id!);
  }

  nomProprietaire(id: number): string {
    return this.proprietaires().find(p => p.id === id)?.nom ?? '—';
  }

  // ── Bien ────────────────────────────────────────────────────────────────
  openCreateBien(): void {
    this.bienForm = {
      nom: '', adresse: '', ville: '', code_postal: '', description: '',
      commission_gestion_pct: 0, commission_gestion_fixe: 0,
      valorisation_heure_proprietaire: 0,
      poids_quote_part_pct: 100, poids_investissement_financier_pct: 0,
      poids_investissement_temporel_pct: 0,
    };
    this.editingBien.set(null);
    this.showBienModal.set(true);
  }

  openEditBien(bien: Bien): void {
    this.bienForm = { ...bien };
    this.editingBien.set(bien);
    this.showBienModal.set(true);
  }

  saveBien(): void {
    const id = this.editingBien()?.id;
    const obs = id ? this.api.updateBien(id, this.bienForm) : this.api.createBien(this.bienForm);
    obs.subscribe({
      next: () => { this.showBienModal.set(false); this.load(); },
      error: err => this.error.set(err?.error?.non_field_errors?.[0] ?? "Échec de l'enregistrement du bien."),
    });
  }

  deleteBien(bien: Bien): void {
    if (!confirm(`Supprimer ${bien.nom} ? Appartements, quote-parts et données rattachées seront aussi supprimés.`)) return;
    this.api.deleteBien(bien.id!).subscribe({ next: () => this.load() });
  }

  closeBienModal(): void { this.showBienModal.set(false); }

  // ── Quote-parts ─────────────────────────────────────────────────────────
  openCreatePart(bien: Bien): void {
    this.partBienId = bien.id!;
    this.partForm = { bien: bien.id, proprietaire: undefined, quote_part_pct: 0 };
    this.editingPart.set(null);
    this.showPartModal.set(true);
  }

  openEditPart(part: PartProprietaire): void {
    this.partBienId = part.bien;
    this.partForm = { ...part };
    this.editingPart.set(part);
    this.showPartModal.set(true);
  }

  savePart(): void {
    const id = this.editingPart()?.id;
    const obs = id
      ? this.api.updatePartProprietaire(id, this.partForm)
      : this.api.createPartProprietaire(this.partForm as PartProprietaire);
    obs.subscribe({
      next: () => { this.showPartModal.set(false); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement de la quote-part."),
    });
  }

  deletePart(part: PartProprietaire): void {
    if (!confirm('Retirer cette quote-part ?')) return;
    this.api.deletePartProprietaire(part.id!).subscribe({ next: () => this.load() });
  }

  closePartModal(): void { this.showPartModal.set(false); }

  // ── Appartements ────────────────────────────────────────────────────────
  openCreateAppart(bien: Bien): void {
    this.appartBienId = bien.id!;
    this.appartForm = { bien: bien.id, nom: '', capacite: null, description: '', airbnb_ical_url: '' };
    this.editingAppart.set(null);
    this.showAppartModal.set(true);
  }

  openEditAppart(appart: Appartement): void {
    this.appartBienId = appart.bien;
    this.appartForm = { ...appart };
    this.editingAppart.set(appart);
    this.showAppartModal.set(true);
  }

  saveAppart(): void {
    const id = this.editingAppart()?.id;
    const obs = id
      ? this.api.updateAppartement(id, this.appartForm)
      : this.api.createAppartement(this.appartForm as Appartement);
    obs.subscribe({
      next: () => { this.showAppartModal.set(false); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement de l'appartement."),
    });
  }

  deleteAppart(appart: Appartement): void {
    if (!confirm(`Supprimer l'appartement ${appart.nom} ?`)) return;
    this.api.deleteAppartement(appart.id!).subscribe({ next: () => this.load() });
  }

  closeAppartModal(): void { this.showAppartModal.set(false); }
}
