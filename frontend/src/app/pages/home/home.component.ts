import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { KeycloakService } from '../../core/keycloak.service';
import { ApiService, Bien, Reservation, Tache, Frais } from '../../core/api.service';

interface ResumeBien {
  bien: Bien;
  reservationsAVenir: number;
  tachesEnCours: number;
  fraisNonRembourses: number;
  capitalTotal: number | string;
}

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.scss',
})
export class HomeComponent implements OnInit {
  private api = inject(ApiService);
  private kc = inject(KeycloakService);

  resumes = signal<ResumeBien[]>([]);
  loading = signal(true);

  get username(): string { return this.kc.username; }
  get email(): string { return this.kc.email; }
  get groups(): string[] { return this.kc.groups; }

  ngOnInit(): void {
    this.api.getBiens().subscribe({
      next: biens => this.chargerResumes(biens),
      error: () => this.loading.set(false),
    });
  }

  private chargerResumes(biens: Bien[]): void {
    if (biens.length === 0) { this.loading.set(false); return; }

    const dans30j = new Date();
    dans30j.setDate(dans30j.getDate() + 30);
    const dans30jIso = dans30j.toISOString().slice(0, 10);
    const aujourdhui = new Date().toISOString().slice(0, 10);

    this.api.getFraisList().subscribe({
      next: tousFrais => {
        const appels = biens.map(bien => forkJoin({
          reservations: this.api.getReservations(bien.id),
          taches: this.api.getTaches({ bien: bien.id }),
          bilan: this.api.getBilan(bien.id!),
        }));

        forkJoin(appels).subscribe({
          next: resultats => {
            const resumes: ResumeBien[] = resultats.map((r, i) => {
              const idsTaches = new Set(r.taches.map((t: Tache) => t.id));
              const reservationsAVenir = r.reservations.filter((res: Reservation) =>
                res.statut === 'confirmee' && res.date_debut >= aujourdhui && res.date_debut <= dans30jIso,
              ).length;
              const tachesEnCours = r.taches.filter((t: Tache) => t.statut === 'a_faire' || t.statut === 'en_cours').length;
              const fraisNonRembourses = tousFrais.filter((f: Frais) =>
                f.payeur === 'proprietaire' && !f.est_rembourse && idsTaches.has(f.tache),
              ).length;
              return { bien: biens[i], reservationsAVenir, tachesEnCours, fraisNonRembourses, capitalTotal: r.bilan.capital_total };
            });
            this.resumes.set(resumes);
            this.loading.set(false);
          },
          error: () => this.loading.set(false),
        });
      },
      error: () => this.loading.set(false),
    });
  }
}
