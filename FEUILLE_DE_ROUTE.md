# Feuille de route — conciergerie

Ce fichier est lu par une machine : `validate-app.sh` refuse de déployer s'il manque ou s'il ne
contient aucun lot au format ci-dessous, et `app-test.sh` lit la commande de test. Garder les titres
tels quels.

App livrée avant l'obligation de feuille de route : le Lot 1 résume l'existant. Les lots suivants
viennent de l'analyse du 2026-10-31 ; la vue d'ensemble est dans `to_do_global.md`, à la racine du
dépôt du lab. Légende : [C] constaté, [D] déduit.

## Commande de test
python manage.py test

## Lot 1 — Existant : biens, séjours Airbnb, tâches, frais, bilan   ·   statut : livré
**Objectif :** gestion locative à plusieurs propriétaires : biens et appartements, synchronisation
Airbnb par iCal, tâches, frais et factures (dans `storage`), remboursements, bilan en grand livre de
capital. Accès : `proprietaires,admins`.
**Tâches :**
- [x] Fonctionnalités en service ; correctifs du chantier tests (dont l'URL iCal vers une destination interne)
**Tests à passer avant de clore le lot :**
- [x] 141 tests backend (10 fichiers) ; `cloisonnement.spec.ts` identique au gabarit
**Vérifié le :** 2026-10-31 — relevé par lecture (version en service = `c0235cf`), tests non rejoués.

## Lot 2 — Confidentialité entre co-propriétaires   ·   statut : livré
**Objectif :** un co-propriétaire ne voit que ce qui le concerne. Règles tranchées par le
propriétaire le 2026-10-31 (chantier « failles ») : `airbnb_ical_url` au gestionnaire seul ; les
`notes` du gestionnaire sur les co-propriétaires au gestionnaire seul ; les contacts (nom, email,
téléphone) visibles entre co-propriétaires d'un même bien, jamais ceux d'un autre bien.
**Constat [C] :**
- `airbnb_ical_url`, un lien secret, est rendu aux co-propriétaires (`serializers.py:41`).
- Les email, téléphone et `notes` du gestionnaire sur les autres co-propriétaires sont visibles dans
  les parts : point laissé au propriétaire par `conciergerie-builder.md`.
**Tâches :**
- [x] Masquer `airbnb_ical_url` à un non-gestionnaire (`airbnb_ical_configure` le remplace) ; une erreur de synchronisation qui recopie l'URL n'est rendue que sous forme générique
- [x] `notes` masquées aux non-gestionnaires, `/api/me/` compris ; fiche d'un propriétaire sans bien commun rendue `null` (parts, responsable de tâche, payeur de frais)
**Tests à passer avant de clore le lot :**
- [x] Un co-propriétaire ne reçoit ni l'URL iCal, ni les notes, ni les coordonnées des co-propriétaires d'un autre bien ; le gestionnaire reçoit tout (`test_confidentialite.py`)
**Vérifié le :** 2026-11-01 — non déployé (tests backend verts par `chantiers/tests/essai.sh`).

## Lot 3 — Justesse du bilan   ·   statut : à faire
**Objectif :** le grand livre rend compte de tous les mouvements, sans erreur d'arrondi.
**Constat [C, d'après le rapport] :**
- `Remboursement.montant` n'entre jamais dans le grand livre : un remboursement sans frais lié n'a aucun effet.
- Les montants du bilan sont des flottants.
**Tâches :**
- [ ] Ⓟ Règle du remboursement sans frais lié
- [ ] Montants en `Decimal`, rendus en chaînes
**Tests à passer avant de clore le lot :**
- [ ] Bilan d'un remboursement sans frais lié conforme à la règle retenue ; montants rendus en chaînes décimales
**Vérifié le :**

## Lot 4 — iCal sans ré-association DNS   ·   statut : livré
**Objectif :** fermer la limite connue de la protection contre les adresses internes.
**Constat [C] :** le nom est résolu deux fois, une fois pour le contrôle et une fois par `requests`.
**Tâches :**
- [x] Se connecter à l'IP déjà contrôlée, en gardant le SNI et l'en-tête `Host` (certificat vérifié contre le nom), à chaque redirection ; ni mandataire ni `.netrc` de l'environnement
- [x] Aucune erreur enregistrée ou journalisée ne recopie plus l'URL secrète (`ical_fetch.message_erreur`)
- [x] Flux borné à 5 Mo, lu par morceaux (`Content-Length` contrôlé d'abord) ; délai total de 30 s par étape, en-têtes compris
**Tests à passer avant de clore le lot :**
- [x] Un DNS simulé dont la réponse change entre deux résolutions est refusé ; une redirection vers une adresse interne est refusée (`test_ical_epinglage.py`, vrais sockets et vrai TLS) ; 5 Mo acceptés, 5 Mo + 1 octet refusés, flux sans `Content-Length` coupé, flux goutte à goutte coupé au délai
**Vérifié le :** 2026-11-01 — non déployé (tests backend verts par `chantiers/tests/essai.sh`).

## Lot 5 — Gabarit front, documentation et chemin à portes   ·   statut : à faire
**Objectif :** même comportement que le gabarit corrigé (T3), un README, un déploiement par le moteur (T1).
**Tâches :**
- [x] `git rm --cached .keycloak-client-opts` — constaté le 2026-11-01 : le fichier n'est plus suivi (`git ls-files`)
- [x] Polices en chemin relatif ; `redirectUri` sur la page courante (`keycloak.service.ts:32`) ; `tokenBases()` ; PKCE (chantier « failles », I2 ; non déployé)
- [x] Ports liés à `${LAB_BIND_ADDR:-127.0.0.1}` (chantier « failles », I1 ; non déployé)
- [ ] `README.md` : rôles (gestionnaire, co-propriétaire), synchronisation iCal, bilan
**Tests à passer avant de clore le lot :**
- [ ] `validate-app.sh conciergerie --quality-only` vert ; lien direct conservé ; cloisonnement `ok` par le moteur
**Vérifié le :**
