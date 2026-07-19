import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, Bien, Appartement, Reservation, Tache } from '../../core/api.service';

interface Ligne {
  label: string;
  appartementId: number | null;
}

@Component({
  selector: 'app-calendrier',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './calendrier.component.html',
  styleUrl: './calendrier.component.scss',
})
export class CalendrierComponent implements OnInit {
  private api = inject(ApiService);

  biens = signal<Bien[]>([]);
  selectedBienId: number | null = null;
  reservations = signal<Reservation[]>([]);
  taches = signal<Tache[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);

  currentMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);

  ngOnInit(): void {
    this.api.getBiens().subscribe({
      next: b => {
        this.biens.set(b);
        this.selectedBienId = b[0]?.id ?? null;
        this.load();
      },
      error: () => { this.error.set('Impossible de charger les biens.'); this.loading.set(false); },
    });
  }

  get appartementsDuBien(): Appartement[] {
    return this.biens().find(b => b.id === this.selectedBienId)?.appartements ?? [];
  }

  get lignes(): Ligne[] {
    return [
      { label: 'Bien entier', appartementId: null },
      ...this.appartementsDuBien.map(a => ({ label: a.nom, appartementId: a.id! })),
    ];
  }

  get daysInMonth(): Date[] {
    const y = this.currentMonth.getFullYear();
    const m = this.currentMonth.getMonth();
    const count = new Date(y, m + 1, 0).getDate();
    return Array.from({ length: count }, (_, i) => new Date(y, m, i + 1));
  }

  get monthLabel(): string {
    return this.currentMonth.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  }

  isToday(day: Date): boolean {
    const t = new Date();
    return day.getFullYear() === t.getFullYear() && day.getMonth() === t.getMonth() && day.getDate() === t.getDate();
  }

  private iso(day: Date): string {
    return `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
  }

  reservationPour(appartementId: number | null, day: Date): Reservation | null {
    if (appartementId === null) return null;
    const iso = this.iso(day);
    return this.reservations().find(
      r => r.appartement === appartementId && r.date_debut <= iso && iso < r.date_fin,
    ) ?? null;
  }

  tachesPour(appartementId: number | null, day: Date): Tache[] {
    const iso = this.iso(day);
    return this.taches().filter(t => t.date_prevue === iso && (t.appartement ?? null) === appartementId);
  }

  prevMonth(): void {
    this.currentMonth = new Date(this.currentMonth.getFullYear(), this.currentMonth.getMonth() - 1, 1);
    this.load();
  }

  nextMonth(): void {
    this.currentMonth = new Date(this.currentMonth.getFullYear(), this.currentMonth.getMonth() + 1, 1);
    this.load();
  }

  goToday(): void {
    const now = new Date();
    this.currentMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    this.load();
  }

  load(): void {
    if (!this.selectedBienId) { this.loading.set(false); return; }
    this.loading.set(true);
    this.api.getReservations(this.selectedBienId).subscribe({ next: r => this.reservations.set(r) });
    this.api.getTaches({ bien: this.selectedBienId }).subscribe({
      next: t => { this.taches.set(t); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger le calendrier.'); this.loading.set(false); },
    });
  }
}
