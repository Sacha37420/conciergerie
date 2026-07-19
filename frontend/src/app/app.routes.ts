import { Routes } from '@angular/router';
import { HomeComponent } from './pages/home/home.component';
import { ProfileComponent } from './pages/profile/profile.component';
import { BiensComponent } from './pages/biens/biens.component';
import { ProprietairesComponent } from './pages/proprietaires/proprietaires.component';
import { EntreprisesComponent } from './pages/entreprises/entreprises.component';
import { ReservationsComponent } from './pages/reservations/reservations.component';

export const routes: Routes = [
  { path: '',              component: HomeComponent },
  { path: 'biens',         component: BiensComponent },
  { path: 'reservations',  component: ReservationsComponent },
  { path: 'proprietaires', component: ProprietairesComponent },
  { path: 'entreprises',   component: EntreprisesComponent },
  { path: 'profile',       component: ProfileComponent },
  { path: '**',            redirectTo: '' },
];
