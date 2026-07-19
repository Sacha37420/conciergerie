import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KeycloakService } from '../../core/keycloak.service';
import {
  ApiService, Bien, Bilan, ApportInitial, VersementRevenu,
} from '../../core/api.service';

@Component({
  selector: 'app-bilan',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './bilan.component.html',
  styleUrl: './bilan.component.scss',
})
export class BilanComponent implements OnInit {
  private api = inject(ApiService);
  protected kc = inject(KeycloakService);

  biens = signal<Bien[]>([]);
  selectedBienId: number | null = null;
  bilan = signal<Bilan | null>(null);
  apports = signal<ApportInitial[]>([]);
  versements = signal<VersementRevenu[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);

  showVersementModal = signal(false);
  versementForm: Partial<VersementRevenu> = {};

  showApportModal = signal(false);
  apportForm: Partial<ApportInitial> = {};

  get isManager(): boolean { return this.kc.isManager; }

  nomProprietaire(id: number): string {
    return this.bilan()?.proprietaires.find(p => p.proprietaire_id === id)?.proprietaire_nom ?? '—';
  }

  ngOnInit(): void {
    this.api.getBiens().subscribe({
      next: b => {
        this.biens.set(b);
        this.selectedBienId = b[0]?.id ?? null;
        this.loadBilan();
      },
      error: () => { this.error.set('Impossible de charger les biens.'); this.loading.set(false); },
    });
  }

  loadBilan(): void {
    if (!this.selectedBienId) { this.loading.set(false); return; }
    this.loading.set(true);
    this.api.getBilan(this.selectedBienId).subscribe({
      next: b => { this.bilan.set(b); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger le bilan.'); this.loading.set(false); },
    });
    if (this.isManager) {
      this.api.getApportsInitiaux(this.selectedBienId).subscribe({ next: a => this.apports.set(a) });
      this.api.getVersementsRevenu(this.selectedBienId).subscribe({ next: v => this.versements.set(v) });
    }
  }

  openVersement(proprietaireId: number): void {
    this.versementForm = {
      bien: this.selectedBienId!, proprietaire: proprietaireId, montant: 0,
      date_versement: new Date().toISOString().slice(0, 10), moyen_paiement: 'virement', notes: '',
    };
    this.showVersementModal.set(true);
  }

  saveVersement(): void {
    this.api.createVersementRevenu(this.versementForm).subscribe({
      next: () => { this.showVersementModal.set(false); this.loadBilan(); },
      error: () => this.error.set("Échec de l'enregistrement du versement."),
    });
  }

  closeVersementModal(): void { this.showVersementModal.set(false); }

  openApport(proprietaireId: number): void {
    this.apportForm = {
      bien: this.selectedBienId!, proprietaire: proprietaireId, montant: 0,
      date: new Date().toISOString().slice(0, 10), notes: '',
    };
    this.showApportModal.set(true);
  }

  saveApport(): void {
    this.api.createApportInitial(this.apportForm).subscribe({
      next: () => { this.showApportModal.set(false); this.loadBilan(); },
      error: () => this.error.set("Échec de l'enregistrement de l'apport."),
    });
  }

  closeApportModal(): void { this.showApportModal.set(false); }

  deleteApport(a: ApportInitial): void {
    if (!confirm('Supprimer cet apport ?')) return;
    this.api.deleteApportInitial(a.id!).subscribe({ next: () => this.loadBilan() });
  }

  deleteVersement(v: VersementRevenu): void {
    if (!confirm('Supprimer ce versement ?')) return;
    this.api.deleteVersementRevenu(v.id!).subscribe({ next: () => this.loadBilan() });
  }
}
