# Rapport Technique — Système de Surveillance et d'Accès Interne (Car Horizon)

Ce rapport présente l'architecture, les technologies utilisées, les difficultés rencontrées et les perspectives d'évolution pour le système de surveillance interne développé pour **Car Horizon**.

---

## 1. Présentation du Projet

Le système est une application web interne de niveau production, sécurisée et centralisée, permettant de gérer :

1. **La surveillance vidéo en direct** via deux caméras IP utilisant le protocole RTSP, diffusées en MJPEG par Gunicorn directement.
2. **Le contrôle d'accès intelligent** par détection et lecture de QR codes (badges) sur la Caméra 1 (entrée), validés dynamiquement en base de données MySQL.
3. **La détection d'intrusions nocturnes** sur la Caméra 2 (garage) de 20h00 à 07h00, à l'aide du détecteur HOG d'OpenCV avec dessin de boîtes englobantes sur le flux vidéo.
4. **L'envoi d'alertes email instantanées** via l'API transactionnelle Brevo — en cas d'intrusion (avec photo jointe) et en cas de QR code inconnu.
5. **La journalisation centralisée** en base MySQL (table `device_logs`) et par fichiers CSV téléchargeables.
6. **Une interface d'administration** pour gérer les employés et leurs badges depuis le navigateur.
7. **Une sécurité par mot de passe** pour l'accès au tableau de bord.

---

## 2. Technologies Utilisées

### Backend & Logique
- **Python 3.10** : Langage principal du projet.
- **Flask (v3.0.3)** : Framework web pour le routage, l'API REST et le rendu des templates.
- **Gunicorn (v22.0.0)** : Serveur WSGI de production (1 worker, 4 threads). Expose directement l'application sur le port `5001`.
- **OpenCV (headless)** : Capture des flux RTSP, décodage des QR codes (`cv2.QRCodeDetector`) et détection de silhouettes HOG (`cv2.HOGDescriptor`).
- **Multi-threading** : Capture vidéo continue et envoi des alertes en threads démons asynchrones pour ne jamais bloquer le flux vidéo.

### Notifications
- **Brevo API (v3)** : Envoi des emails d'alerte via l'API transactionnelle REST (HTTP POST). Authentification par clé API. L'accès IPv4 est forcé au niveau de `socket.getaddrinfo` pour contourner le filtrage IPv6 de Brevo.
- **Destinataires** : 5 adresses email configurées via la variable d'environnement `NOTIFICATION_EMAILS`.
- **Expéditeur** : `carhorizonalert@gmail.com` (domaine vérifié sur Brevo).

### Stockage & Base de données
- **MySQL 8.0** : Stocke la table `device_logs` (journaux système) et `employees` (badge, nom, prénom, service).
- **CSV locaux** : `historique_acces.csv` et `historique_intrusion.csv` — archivage local téléchargeable depuis l'interface.

### Frontend
- **HTML5 / CSS3** : Interface en mode sombre (dark mode premium), variables CSS, typographie Google Fonts.
- **JavaScript Vanilla** :
  - Simulation CCTV sur canvas (effet neige / radar) si les caméras sont hors ligne.
  - Polling AJAX toutes les secondes pour afficher les scans de badges en temps réel.
  - Overlay de validation (vert/rouge) sur le flux vidéo lors d'un scan.

### Infrastructure
- **Docker & Docker Compose** : Orchestration de deux services (`db` MySQL et `web` Gunicorn). Pas de reverse proxy : Gunicorn écoute directement sur `192.168.32.35:5001`.

---

## 3. Architecture du Système

```
Navigateur client
      │
      ▼
Gunicorn :5001  (192.168.32.35)
      │
      ├── Flask Routes (login, surveillance, API, admin)
      ├── Thread 1 — run_badge_scanner()   → Caméra 1 RTSP → QR detect → process_badge_scan()
      └── Thread 2 — run_intrusion_alarm() → Caméra 2 RTSP → HOG detect → send_alert()
                                                                    │
                                                             Brevo API REST
                                                          (5 destinataires email)
```

---

## 4. Alertes Email — Fonctionnement

La fonction unifiée `send_alert(subject, body, attachment_bytes)` gère les deux types d'alertes :

| Déclencheur | Sujet de l'email | Pièce jointe |
| :--- | :--- | :--- |
| QR code inconnu à l'entrée | `[ALERTE QR INCONNU] Car Horizon - <horodatage>` | Aucune |
| Silhouette humaine détectée (20h–07h) | `[ALERTE INTRUSION] Car Horizon - <horodatage>` | Capture JPEG avec boîte rouge |

Les alertes sont envoyées dans un thread démon asynchrone avec un cooldown de 30 secondes pour l'intrusion (anti-spam).

---

## 5. Commandes de Déploiement

### Construire et démarrer l'infrastructure :
```bash
docker compose up -d --build
```

### Arrêter et supprimer les volumes (réinitialiser la BDD) :
```bash
docker compose down -v
```

### Consulter les logs en temps réel :
```bash
docker compose logs -f
```

### Simuler un scan de badge :
```bash
# Badge autorisé
curl http://192.168.32.35:5001/scan/EMP001

# Badge inconnu (déclenche une alerte email)
curl http://192.168.32.35:5001/scan/BADGE-INCONNU
```

---

## 6. Difficultés Rencontrées et Solutions Apportées

| Difficulté | Impact | Solution |
| :--- | :--- | :--- |
| **Isolement mémoire Gunicorn multi-workers** | Plusieurs workers = plusieurs instances des threads caméra, conflits RTSP, état non partagé. | Gunicorn limité à `--workers 1` avec `--threads 4` pour absorber les connexions concurrentes. |
| **Surcharge CPU du détecteur HOG** | Analyse sur image Full HD = < 1 fps. | Redimensionnement à 400 px de large uniquement pour le calcul HOG. Les boîtes sont ensuite remises à l'échelle sur l'image d'origine. |
| **Blocage vidéo lors des envois d'alertes** | Les appels réseau Brevo sont bloquants et causaient des saccades. | Envois exécutés dans un `threading.Thread(daemon=True)` lancé à la volée. |
| **Brevo refuse les connexions IPv6** | L'IP IPv6 de la machine n'est pas dans la liste blanche Brevo. | `socket.getaddrinfo` est remplacé temporairement par une version forçant `AF_INET` (IPv4) avant chaque appel API Brevo. |
| **SMTP Gmail bloquait les connexions** | Gmail exige OAuth2 pour les comptes modernes, les mots de passe simples sont rejetés. | Remplacement de SMTP par l'API transactionnelle REST Brevo — plus fiable et sans gestion de certificat. |

---

## 7. Évolutions Futures

1. **HTTPS / TLS** : Ajouter un certificat SSL (Let's Encrypt ou auto-signé) au niveau de Gunicorn ou d'un reverse proxy léger comme Caddy.
2. **Gestion des rôles** : Séparer les accès en deux profils — *Opérateur* (lecture seule du tableau de bord) et *Administrateur* (gestion des employés et configuration).
3. **Table SQL pour les intrusions** : Remplacer le fichier `historique_intrusion.csv` par une table `intrusion_alerts` en MySQL pour un historique plus riche et interrogeable.
4. **Chiffrement du disque** : Activer BitLocker (Windows) ou LUKS (Linux) sur la machine hôte pour protéger les données en cas d'accès physique non autorisé.

---

## 8. Sources et Références

### Frameworks & Bibliothèques Python

| Bibliothèque | Version | Lien |
| :--- | :--- | :--- |
| Flask | 3.0.3 | https://flask.palletsprojects.com/ |
| Gunicorn | 22.0.0 | https://gunicorn.org/ |
| OpenCV (headless) | 4.9.0.80 | https://opencv.org/ — https://github.com/opencv/opencv |
| mysql-connector-python | 8.4.0 | https://dev.mysql.com/doc/connector-python/en/ |
| NumPy | < 2.0.0 | https://numpy.org/ |
| requests | 2.32.3 | https://requests.readthedocs.io/ |

### Infrastructure & DevOps

| Outil | Lien |
| :--- | :--- |
| Docker Engine | https://docs.docker.com/engine/ |
| Docker Compose | https://docs.docker.com/compose/ |
| Image officielle Python 3.10-slim | https://hub.docker.com/_/python |
| Image officielle MySQL 8.0 | https://hub.docker.com/_/mysql |

### API & Services Externes

| Service | Usage | Lien |
| :--- | :--- | :--- |
| Brevo (ex-Sendinblue) | Envoi des emails d'alerte transactionnels | https://www.brevo.com/ |
| Brevo API v3 — Transactional Email | Documentation de l'endpoint REST utilisé | https://developers.brevo.com/reference/sendtransacemail |

### Documentation Technique Consultée

| Sujet | Source |
| :--- | :--- |
| HOG People Detector — OpenCV | https://docs.opencv.org/4.x/d5/d33/structcv_1_1HOGDescriptor.html |
| QR Code Detector — OpenCV | https://docs.opencv.org/4.x/de/dc3/classcv_1_1QRCodeDetector.html |
| MJPEG streaming avec Flask | https://flask.palletsprojects.com/en/3.0.x/patterns/streaming/ |
| Capture RTSP avec OpenCV | https://docs.opencv.org/4.x/d8/dfe/classcv_1_1VideoCapture.html |
| Flask Sessions & Authentification | https://flask.palletsprojects.com/en/3.0.x/quickstart/#sessions |
| MySQL Connector — Requêtes paramétrées | https://dev.mysql.com/doc/connector-python/en/connector-python-api-mysqlcursor-execute.html |
| Docker — Réseau multi-services | https://docs.docker.com/compose/networking/ |
| HOG + Linear SVM — Article original (Dalal & Triggs, 2005) | https://lear.inrialpes.fr/people/triggs/pubs/Dalal-cvpr05.pdf |
| socket.getaddrinfo — Documentation Python | https://docs.python.org/3/library/socket.html#socket.getaddrinfo |

### Design & Typographie

| Ressource | Détail | Lien |
| :--- | :--- | :--- |
| Google Fonts — Barlow Condensed | Titres et labels de l'interface | https://fonts.google.com/specimen/Barlow+Condensed |
| Google Fonts — Barlow | Corps de texte de l'interface | https://fonts.google.com/specimen/Barlow |
| Google Fonts — Source Code Pro | Affichage des logs système (monospace) | https://fonts.google.com/specimen/Source+Code+Pro |

