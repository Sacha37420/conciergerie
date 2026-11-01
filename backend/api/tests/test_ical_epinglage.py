"""Lot 4 — la connexion se fait à l'adresse contrôlée, jamais à une seconde
résolution du nom (ré-association DNS).

Vrais sockets, vrai TLS, vrai `requests` : un serveur local (boucle locale du
conteneur de test) joue le site du calendrier. Le DNS est simulé
(`ical_fetch._resoudre`) et l'ouverture du socket par urllib3 est observée :
elle reçoit l'adresse épinglée, que le test redirige vers le serveur local.
Toute résolution réelle du nom (`socket.getaddrinfo('ical.example')`) est
relevée — il ne doit y en avoir aucune.
"""
import datetime
import os
import socket
import ssl
import tempfile
import threading
import time
from unittest import mock

import urllib3.util.connection
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.test import SimpleTestCase

from api import ical_fetch

PUBLIQUE = '34.120.10.20'
AUTRE_PUBLIQUE = '34.120.10.21'
NOM = 'ical.example'
CORPS = b'BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n'


def _certificats(dossier: str, nom: str) -> tuple[str, str, str]:
    """Autorité locale + certificat serveur pour `nom`. Rend (ca, cert, clé)."""
    maintenant = datetime.datetime.now(datetime.timezone.utc)
    cle_ca = ec.generate_private_key(ec.SECP256R1())
    sujet_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Autorite de test')])
    ca = (x509.CertificateBuilder().subject_name(sujet_ca).issuer_name(sujet_ca)
          .public_key(cle_ca.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(maintenant - datetime.timedelta(days=1))
          .not_valid_after(maintenant + datetime.timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                       content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(cle_ca.public_key()), critical=False)
          .sign(cle_ca, hashes.SHA256()))
    cle = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nom)]))
            .issuer_name(sujet_ca).public_key(cle.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(maintenant - datetime.timedelta(days=1))
            .not_valid_after(maintenant + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(nom)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(cle_ca.public_key()),
                           critical=False)
            .sign(cle_ca, hashes.SHA256()))
    chemins = [os.path.join(dossier, f) for f in (f'ca-{nom}.pem', f'cert-{nom}.pem', f'cle-{nom}.pem')]
    with open(chemins[0], 'wb') as f:
        f.write(ca.public_bytes(serialization.Encoding.PEM))
    with open(chemins[1], 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(chemins[2], 'wb') as f:
        f.write(cle.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    return tuple(chemins)


class ServeurLocal:
    """Serveur HTTP(S) minimal sur 127.0.0.1 : relève le SNI et l'en-tête
    `Host` de chaque requête, répond par la réponse suivante de la file."""

    def __init__(self, reponses, cert=None, cle=None):
        self.reponses = list(reponses)
        self.requetes = []          # (sni, host, ligne de requête)
        self.sock = socket.create_server(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self.ctx = None
        if cert:
            self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ctx.load_cert_chain(cert, cle)
            self.ctx.sni_callback = self._sni
        self._sni_courant = None
        self.fil = threading.Thread(target=self._boucle, daemon=True)
        self.fil.start()

    def _sni(self, sslsock, nom, ctx):
        self._sni_courant = nom

    def _boucle(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            conn.settimeout(5)
            try:
                self._sni_courant = None
                if self.ctx:
                    conn = self.ctx.wrap_socket(conn, server_side=True)
                donnees = b''
                while b'\r\n\r\n' not in donnees:
                    morceau = conn.recv(4096)
                    if not morceau:
                        break
                    donnees += morceau
                lignes = donnees.decode('latin-1').split('\r\n')
                host = next((l.split(':', 1)[1].strip() for l in lignes[1:] if l.lower().startswith('host:')), None)
                self.requetes.append((self._sni_courant, host, lignes[0]))
                reponse = self.reponses.pop(0) if self.reponses else _http(500)
                if callable(reponse):
                    reponse(conn)       # réponse écrite en flux (taille, lenteur)
                else:
                    conn.sendall(reponse)
            except (OSError, ssl.SSLError):
                pass                # poignée de main refusée par le client : c'est un cas testé
            finally:
                conn.close()

    def fermer(self):
        self.sock.close()


def _http(code, corps=b'', location=None):
    entetes = [f'HTTP/1.1 {code} X', f'Content-Length: {len(corps)}', 'Connection: close']
    if location:
        entetes.append(f'Location: {location}')
    return ('\r\n'.join(entetes) + '\r\n\r\n').encode() + corps


class _AvecServeurLocal(SimpleTestCase):
    """DNS simulé, ouverture du socket observée, serveur local ; aucun test ici."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dossier = tempfile.mkdtemp()
        cls.ca, cls.cert, cls.cle = _certificats(cls.dossier, NOM)
        cls.ca_autre, cls.cert_autre, cls.cle_autre = _certificats(cls.dossier, 'autre.example')

    def setUp(self):
        # DNS simulé : une file de réponses par nom (la dernière se répète).
        self.dns = {NOM: [[PUBLIQUE]]}
        self.resolutions = []

        def resoudre(hote, port):
            self.resolutions.append(hote)
            file = self.dns.get(hote)
            if not file:
                raise OSError('Name or service not known')
            return file.pop(0) if len(file) > 1 else file[0]

        self._patch(mock.patch.object(ical_fetch, '_resoudre', resoudre))

        # Ouverture du socket par urllib3 : relevée, puis redirigée vers le
        # serveur local, quelle que soit l'adresse demandée.
        self.connexions = []
        vraie = urllib3.util.connection.create_connection

        def ouvrir(adresse, *args, **kwargs):
            self.connexions.append(adresse)
            port = self.serveur.port if self.serveur else self.port_ferme
            return vraie(('127.0.0.1', port), *args, **kwargs)

        self._patch(mock.patch.object(urllib3.util.connection, 'create_connection', ouvrir))

        # Toute résolution réelle d'un nom du test serait une seconde résolution.
        self.getaddrinfo_noms = []
        vrai_gai = socket.getaddrinfo

        def gai(hote, *args, **kwargs):
            if isinstance(hote, str) and hote.endswith('.example'):
                self.getaddrinfo_noms.append(hote)
                raise socket.gaierror('résolution réelle interdite dans ce test')
            return vrai_gai(hote, *args, **kwargs)

        self._patch(mock.patch('socket.getaddrinfo', gai))
        self._patch(mock.patch.object(ical_fetch, 'VERIFIER_CERTIFICAT', self.ca))
        self.serveur = None

    def tearDown(self):
        if self.serveur:
            self.serveur.fermer()
        self.assertEqual(self.getaddrinfo_noms, [], 'le nom a été résolu une seconde fois')

    def _patch(self, p):
        p.start()
        self.addCleanup(p.stop)

    def servir(self, *reponses, tls=True, cert=None, cle=None):
        if tls:
            self.serveur = ServeurLocal(reponses, cert or self.cert, cle or self.cle)
        else:
            self.serveur = ServeurLocal(reponses)
        return self.serveur


class EpinglageTests(_AvecServeurLocal):

    # ── HTTPS ────────────────────────────────────────────────────────────
    def test_https_connexion_a_l_adresse_controlee_sni_host_et_certificat_du_nom(self):
        serveur = self.servir(_http(200, CORPS))
        self.assertEqual(ical_fetch.telecharger(f'https://{NOM}/calendar/ical/1.ics?s=secret'), CORPS)
        self.assertEqual(self.connexions, [(PUBLIQUE, 443)])
        self.assertEqual(self.resolutions, [NOM])
        self.assertEqual(serveur.requetes, [(NOM, NOM, 'GET /calendar/ical/1.ics?s=secret HTTP/1.1')])

    def test_port_explicite_conserve_dans_host(self):
        serveur = self.servir(_http(200, CORPS))
        ical_fetch.telecharger(f'https://{NOM}:8443/c.ics')
        self.assertEqual(self.connexions, [(PUBLIQUE, 8443)])
        self.assertEqual(serveur.requetes[0][:2], (NOM, f'{NOM}:8443'))

    def test_certificat_verifie_contre_le_nom_pas_contre_l_adresse(self):
        # Le serveur à l'adresse épinglée présente un certificat valide (même
        # autorité) mais pour un autre nom : refusé.
        self._patch(mock.patch.object(ical_fetch, 'VERIFIER_CERTIFICAT', self.ca_autre))
        self.servir(_http(200, CORPS), cert=self.cert_autre, cle=self.cle_autre)
        with self.assertRaises(ical_fetch.requests.exceptions.SSLError) as cm:
            ical_fetch.telecharger(f'https://{NOM}/c.ics')
        self.assertEqual(ical_fetch.message_erreur(cm.exception), 'Certificat du serveur du calendrier refusé.')

    def test_certificat_d_une_autorite_inconnue_refuse(self):
        self._patch(mock.patch.object(ical_fetch, 'VERIFIER_CERTIFICAT', self.ca_autre))
        self.servir(_http(200, CORPS))
        with self.assertRaises(ical_fetch.requests.exceptions.SSLError):
            ical_fetch.telecharger(f'https://{NOM}/c.ics')

    def test_mandataire_de_l_environnement_ignore(self):
        self.servir(_http(200, CORPS))
        with mock.patch.dict(os.environ, {'HTTPS_PROXY': 'http://mandataire.example:3128',
                                          'HTTP_PROXY': 'http://mandataire.example:3128',
                                          'ALL_PROXY': 'http://mandataire.example:3128'}):
            self.assertEqual(ical_fetch.telecharger(f'https://{NOM}/c.ics'), CORPS)
        self.assertEqual(self.connexions, [(PUBLIQUE, 443)])

    # ── Ré-association DNS ───────────────────────────────────────────────
    def test_dns_qui_change_apres_le_controle_n_est_jamais_consulte(self):
        # Publique au contrôle, interne ensuite : la connexion va à la publique.
        for interne in ('10.0.0.5', '127.0.0.1', '172.18.0.7'):
            with self.subTest(interne=interne):
                self.dns[NOM] = [[PUBLIQUE], [interne]]
                self.connexions.clear()
                self.resolutions.clear()
                self.servir(_http(200, CORPS))
                self.assertEqual(ical_fetch.telecharger(f'https://{NOM}/c.ics'), CORPS)
                self.assertEqual(self.connexions, [(PUBLIQUE, 443)])
                self.assertEqual(self.resolutions, [NOM])
                self.serveur.fermer()

    def test_redirection_vers_le_meme_nom_devenu_interne_refusee(self):
        # Le site redirige vers lui-même ; entre-temps le nom désigne un
        # conteneur. La seconde étape est re-contrôlée et refusée.
        for interne in ('10.0.0.5', '127.0.0.1', '172.18.0.7'):
            with self.subTest(interne=interne):
                self.dns[NOM] = [[PUBLIQUE], [interne]]
                self.connexions.clear()
                serveur = self.servir(_http(302, location='/v2/c.ics'), _http(200, b'SECRET'))
                with self.assertRaises(ical_fetch.DestinationInterdite):
                    ical_fetch.telecharger(f'https://{NOM}/c.ics')
                self.assertEqual(self.connexions, [(PUBLIQUE, 443)])
                self.assertEqual(len(serveur.requetes), 1)
                serveur.fermer()

    def test_redirection_publique_epinglee_a_sa_propre_adresse(self):
        self.dns['cdn.example'] = [[AUTRE_PUBLIQUE]]
        serveur = self.servir(_http(301, location='http://cdn.example/c.ics'), _http(200, CORPS), tls=False)
        self.assertEqual(ical_fetch.telecharger(f'http://{NOM}/c.ics'), CORPS)
        self.assertEqual(self.connexions, [(PUBLIQUE, 80), (AUTRE_PUBLIQUE, 80)])
        self.assertEqual([r[1] for r in serveur.requetes], [NOM, 'cdn.example'])

    def test_redirection_vers_une_adresse_interne_refusee(self):
        self.dns.update({'127.0.0.1': [['127.0.0.1']], 'keycloak': [['172.18.0.2']]})
        for cible in ('http://127.0.0.1:8080/admin', 'http://keycloak:8080/', 'http://10.0.0.5/'):
            with self.subTest(cible=cible):
                self.dns['10.0.0.5'] = [['10.0.0.5']]
                self.connexions.clear()
                serveur = self.servir(_http(302, location=cible), _http(200, b'SECRET'))
                with self.assertRaises(ical_fetch.DestinationInterdite):
                    ical_fetch.telecharger(f'https://{NOM}/c.ics')
                self.assertEqual(self.connexions, [(PUBLIQUE, 443)])
                serveur.fermer()

    # ── HTTP ─────────────────────────────────────────────────────────────
    def test_http_connexion_a_l_adresse_controlee_et_host_du_nom(self):
        serveur = self.servir(_http(200, CORPS), tls=False)
        self.assertEqual(ical_fetch.telecharger(f'http://{NOM}/c.ics'), CORPS)
        self.assertEqual(self.connexions, [(PUBLIQUE, 80)])
        self.assertEqual(serveur.requetes, [(None, NOM, 'GET /c.ics HTTP/1.1')])

    def test_code_d_erreur_rapporte_sans_l_url(self):
        self.servir(_http(404), tls=False)
        with self.assertRaises(ical_fetch.ReponseRefusee) as cm:
            ical_fetch.telecharger(f'http://{NOM}/calendar/ical/1.ics?s=secret')
        message = ical_fetch.message_erreur(cm.exception)
        self.assertIn('404', message)
        self.assertNotIn('secret', message)
        self.assertNotIn(NOM, message)

    def test_connexion_refusee_rapportee_sans_l_url(self):
        libre = socket.create_server(('127.0.0.1', 0))
        self.port_ferme = libre.getsockname()[1]
        libre.close()                   # plus rien n'écoute sur ce port
        with self.assertRaises(ical_fetch.requests.ConnectionError) as cm:
            ical_fetch.telecharger(f'http://{NOM}/calendar/ical/1.ics?s=secret')
        self.assertIn('secret', str(cm.exception))      # requests recopie l'URL…
        message = ical_fetch.message_erreur(cm.exception)
        self.assertNotIn('secret', message)             # …le message conservé, non
        self.assertNotIn('calendar/ical', message)


def _sans_longueur(taille_totale, morceau=64 * 1024, pause=0.0):
    """Réponse 200 sans `Content-Length` (fin = fermeture), écrite par
    morceaux jusqu'à `taille_totale` octets ou jusqu'à ce que le client coupe."""
    def ecrire(conn):
        conn.sendall(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')
        envoye = 0
        try:
            while envoye < taille_totale:
                n = min(morceau, taille_totale - envoye)
                conn.sendall(b'x' * n)
                envoye += n
                if pause:
                    time.sleep(pause)
        except OSError:
            pass                    # le client a coupé : c'est ce qui est attendu
        ecrire.envoye = envoye
    ecrire.envoye = 0
    return ecrire


MAX = ical_fetch.MAX_OCTETS


class TailleMaximaleTests(_AvecServeurLocal):
    """Flux borné à 5 Mo, lu par morceaux, délai total borné."""

    def test_cinq_mo_annonces_acceptes(self):
        self.servir(_http(200, b'x' * MAX), tls=False)
        self.assertEqual(len(ical_fetch.telecharger(f'http://{NOM}/c.ics')), MAX)

    def test_cinq_mo_plus_un_octet_annonces_refuses(self):
        self.servir(_http(200, b'x' * (MAX + 1)), tls=False)
        with self.assertRaises(ical_fetch.ReponseRefusee) as cm:
            ical_fetch.telecharger(f'http://{NOM}/calendar/ical/1.ics?s=secret')
        message = ical_fetch.message_erreur(cm.exception)
        self.assertEqual(message, 'Le flux du calendrier dépasse 5 Mo.')
        self.assertNotIn('secret', message)

    def test_content_length_annoncant_trop_refuse_avant_lecture(self):
        # Annonce 1 Go, n'envoie rien : refus sur l'annonce, sans attendre le corps.
        self.servir(b'HTTP/1.1 200 OK\r\nContent-Length: 1073741824\r\n\r\n', tls=False)
        debut = time.monotonic()
        with self.assertRaises(ical_fetch.ReponseRefusee):
            ical_fetch.telecharger(f'http://{NOM}/c.ics')
        self.assertLess(time.monotonic() - debut, 5)

    def test_flux_sans_longueur_de_cinq_mo_accepte(self):
        self.servir(_sans_longueur(MAX), tls=False)
        self.assertEqual(len(ical_fetch.telecharger(f'http://{NOM}/c.ics')), MAX)

    def test_flux_sans_longueur_de_cinq_mo_plus_un_octet_refuse(self):
        self.servir(_sans_longueur(MAX + 1), tls=False)
        with self.assertRaises(ical_fetch.ReponseRefusee):
            ical_fetch.telecharger(f'http://{NOM}/c.ics')

    def test_flux_sans_longueur_qui_depasse_est_coupe(self):
        flux = _sans_longueur(200 * 1024 * 1024)        # 200 Mo proposés
        self.servir(flux, tls=False)
        with self.assertRaises(ical_fetch.ReponseRefusee) as cm:
            ical_fetch.telecharger(f'http://{NOM}/c.ics')
        self.assertEqual(str(cm.exception), 'Le flux du calendrier dépasse 5 Mo.')
        self.serveur.fil.join(10)       # le serveur voit la coupure et rend la main
        # Coupé peu après la borne, loin des 200 Mo (tampons TCP compris).
        self.assertLess(flux.envoye, 40 * 1024 * 1024)

    def test_flux_sans_longueur_qui_depasse_en_https_est_coupe(self):
        self.servir(_sans_longueur(MAX + 1))
        with self.assertRaises(ical_fetch.ReponseRefusee):
            ical_fetch.telecharger(f'https://{NOM}/c.ics')

    def test_flux_goutte_a_goutte_coupe_au_delai_total(self):
        self._patch(mock.patch.object(ical_fetch, 'DELAI_TOTAL', 1))
        flux = _sans_longueur(1000, morceau=10, pause=0.2)   # 20 s s'il allait au bout
        self.servir(flux, tls=False)
        debut = time.monotonic()
        with self.assertRaises(ical_fetch.ReponseRefusee) as cm:
            ical_fetch.telecharger(f'http://{NOM}/calendar/ical/1.ics?s=secret')
        self.assertLess(time.monotonic() - debut, 5)
        self.assertEqual(ical_fetch.message_erreur(cm.exception),
                         'Le serveur du calendrier met trop de temps à envoyer le flux.')

    def test_en_tetes_goutte_a_goutte_coupes_au_delai_total(self):
        self._patch(mock.patch.object(ical_fetch, 'DELAI_TOTAL', 1))

        def en_tetes_sans_fin(conn):
            conn.sendall(b'HTTP/1.1 200 OK\r\n')
            try:
                for i in range(90):
                    conn.sendall(f'X-Lent-{i}: 1\r\n'.encode())
                    time.sleep(0.2)
            except OSError:
                pass
        self.servir(en_tetes_sans_fin, tls=False)
        debut = time.monotonic()
        with self.assertRaises(ical_fetch.ReponseRefusee) as cm:
            ical_fetch.telecharger(f'http://{NOM}/c.ics')
        self.assertLess(time.monotonic() - debut, 5)
        self.assertNotIn(NOM, ical_fetch.message_erreur(cm.exception))

    def test_corps_d_une_redirection_jamais_lu(self):
        # Une redirection suivie d'un corps sans fin : seule la suite est lue.
        def redirection_sans_fin(conn):
            conn.sendall(b'HTTP/1.1 302 Found\r\nLocation: /v2.ics\r\nConnection: close\r\n\r\n')
            try:
                for _ in range(3000):
                    conn.sendall(b'x' * 65536)
            except OSError:
                pass
        self.servir(redirection_sans_fin, _http(200, CORPS), tls=False)
        self.assertEqual(ical_fetch.telecharger(f'http://{NOM}/c.ics'), CORPS)
