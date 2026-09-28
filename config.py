# -*- coding: utf-8 -*-
"""HIKAROCHAT - configuration centrale (v2).
Toutes les cles sensibles viennent de variables d'environnement (.env).
La base SQL ne stocke JAMAIS de photo/image : que le texte necessaire.
"""
import os


def _env(k, d=""):
    return os.environ.get(k, d)


# --- Version ---
VERSION = "1.1.6"

# =====================================================================
#  BASE DE DONNEES
# =====================================================================
# ORDRE DE PRIORITE choisi automatiquement au demarrage :
#   1) MySQL  (si DATABASE_URL / MYSQL_URL est defini)  <-- RECOMMANDE
#   2) Supabase (si SUPABASE_URL + SUPABASE_KEY)
#   3) JSON local data/local.json (dev uniquement, NON persistant sur Render)
#
#  >>> OU METTRE L'URL DE TA BASE MYSQL <<<
#  Sur Render : Dashboard du service -> onglet "Environment" -> "Add Variable"
#     Key   = DATABASE_URL
#     Value = mysql://UTILISATEUR:MOTDEPASSE@HOTE:PORT/NOM_BASE
#  En local : copie .env.example en .env et renseigne DATABASE_URL.
#  Formats acceptes :
#     mysql://user:pass@host:3306/hikarochat
#     mysql+pymysql://user:pass@host:3306/hikarochat
#  IMPORTANT : le disque de Render est EPHEMERE -> sans MySQL, les comptes,
#  points et messages sont perdus a chaque redemarrage. MySQL les rend durables.
DATABASE_URL = _env("DATABASE_URL", "") or _env("MYSQL_URL", "")

# --- Supabase (free tier : texte seulement, pas d'images) ---
# .strip() + rstrip('/') : evite l'erreur PGRST125 si un / ou espace traine a la fin
SUPABASE_URL = _env("SUPABASE_URL").strip().rstrip("/")
SUPABASE_KEY = _env("SUPABASE_KEY").strip()

# --- Support officiel ---
SUPPORT_EMAIL = _env("SUPPORT_EMAIL", "raphaelod760@protonmail.com")
ADMIN_EMAIL = _env("ADMIN_EMAIL", "raphaelod760@protonmail.com")

# --- SMTP (optionnel : sinon file d'attente data/outbox/) ---
SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER")
SMTP_PASS = _env("SMTP_PASS")

# --- Securite ---
SECRET_KEY = _env("SECRET_KEY", "hikaro-dev-secret-change-me")
OWNER_CODE = _env("OWNER_CODE", "HIKARO-OWNER-CODE")   # code secret proprio (inscription owner)
ADMIN_CODE = _env("ADMIN_CODE", "HIKARO-ADMIN-2026")   # code panel/admin
OWNER_PSEUDO = _env("OWNER_PSEUDO", "Raphael")

# --- Reseau ---
HOST = _env("HOST", "0.0.0.0")
PORT = int(_env("PORT", "5000"))

# --- Scaling multi-serveurs (2+ instances) ---
INSTANCE_ID = _env("INSTANCE_ID", "hikaro-1")
# URL Redis (ex: redis://localhost:6379/0) pour que 2 serveurs partagent les
# evenements et qu'une panne de l'un ne bloque pas l'autre. Vide = mono-serveur.
MESSAGE_QUEUE = _env("MESSAGE_QUEUE", "")

# --- Regles ---
MIN_AGE = 13
PREMIUM_LEVEL = 10

# --- Charge / bascule 2e serveur ---
# Au-dela de MAX_CLIENTS sur une instance, /health se declare "sature" et le
# Bot Relais annonce la bascule ; les clients basculent sur BACKUP_URL si defini.
MAX_CLIENTS = int(_env("MAX_CLIENTS", "200"))
BACKUP_URL = _env("BACKUP_URL", "")   # ex: https://s2.hikarochat.app

# --- Capacite GLOBALE de l'appli (limite dure d'utilisateurs simultanes) ---
# Au-dela, toute nouvelle connexion est refusee ("reessaie plus tard").
APP_CAPACITY = int(_env("APP_CAPACITY", "100"))

# --- Economie annonces / salle annonce / casino / cinema ---
ANNONCE_PRIX = int(_env("ANNONCE_PRIX", "150"))      # points pour poster une annonce
CINEMA_TICKET_XP = int(_env("CINEMA_TICKET_XP", "20"))  # XP pour un ticket cine
SECRET_MIN_H = 1
SECRET_MAX_H = 24

# --- Cinema securise ---
# Si True, il faut un billet (XP) pour ENTRER dans le salon cinema et pour
# recevoir/diffuser le flux P2P. Le signaling est refuse sans billet.
CINEMA_SECURE = _env("CINEMA_SECURE", "1") not in ("0", "false", "")

# --- Salle Privee sur reservation (places limitees) ---
SALLE_CAPACITY = int(_env("SALLE_CAPACITY", "10"))          # nb de places
SALLE_PRIX = int(_env("SALLE_PRIX", "100"))                 # points pour reserver
SALLE_RESERVATION_H = int(_env("SALLE_RESERVATION_H", "3"))  # duree d'une reservation (h)

# --- Bans progressifs (selon le nombre de sanctions) ---
# (strike, duree_en_secondes)   0 = permanent
BAN_TIERS = [
    (1, 3600),          # 1h
    (2, 6 * 3600),      # 6h
    (3, 24 * 3600),     # 24h
    (4, 7 * 24 * 3600), # 7j
    (5, 0),             # permanent
]

# --- Salons (le salon Cinema/Divertissement a ses 2 modes ensemble/perso) ---
SALONS = [
    {"id": "general", "nom": "General", "icone": "\U0001F310"},
    {"id": "otaku", "nom": "Otaku", "icone": "\U0001F338"},
    {"id": "manga", "nom": "Manga", "icone": "\U0001F4D6"},
    {"id": "tech", "nom": "Tech", "icone": "\U0001F4BB"},
    {"id": "gaming", "nom": "Gaming", "icone": "\U0001F3AE", "desc": "Jeux, e-sport & parties"},
    {"id": "musique", "nom": "Musique", "icone": "\U0001F3A7", "desc": "Sons, playlists & partages"},
    {"id": "ecole", "nom": "Ecole", "icone": "\U0001F3EB"},
    {"id": "memes", "nom": "Memes", "icone": "\U0001F602",
     "desc": "Le fun, les vannes & les memes"},
    {"id": "chill", "nom": "Chill", "icone": "\U0001F319",
     "desc": "Detente, confidences & bonnes vibes"},
    {"id": "cinema", "nom": "Divertissement", "icone": "\U0001F3AC", "divertissement": True},
    {"id": "casino", "nom": "Casino", "icone": "\U0001F3B0", "casino": True},
    {"id": "annonce", "nom": "Annonces", "icone": "\U0001F4E2", "annonce": True, "prix": ANNONCE_PRIX},
    {"id": "prive", "nom": "Salle Priv\u00e9e", "icone": "\U0001F512", "reservable": True,
     "capacity": SALLE_CAPACITY, "prix": SALLE_PRIX,
     "desc": "Salon a places limitees, sur reservation."},
    {"id": "premium", "nom": "Premium", "icone": "\U0001F451", "level": PREMIUM_LEVEL},
    {"id": "aide", "nom": "Aide", "icone": "\U0001F198"},
    {"id": "admin", "nom": "Admin", "icone": "\U0001F6E1", "admin": True},
    # SALON SECRET : cache par defaut (nom/desc masques), debloque en tapant
    # le mot "secret" dans le chat contre des points.
    {"id": "secret", "nom": "\u2753\u2753\u2753", "icone": "\U0001F576",
     "secret": True, "prix": 500, "desc": "\u2022\u2022\u2022 masque \u2022\u2022\u2022",
     "real_nom": "Club Secret", "real_desc": "Le salon cache de l'elite HIKARO."},
]

# --- L'escouade de bots : ils apparaissent TOUJOURS comme connectes ---
BOTS = [
    {"id": "bot_haiku", "nom": "Haiku", "role": "bot", "icone": "\U0001F9E0",
     "desc": "Cerveau & accueil"},
    {"id": "bot_ambiance", "nom": "Ambiance", "role": "bot", "icone": "\U0001F3B6",
     "desc": "Anime les salons"},
    {"id": "bot_network", "nom": "Network", "role": "bot", "icone": "\U0001F6F0",
     "desc": "Gere le reseau, bans & avertissements"},
    {"id": "bot_eco", "nom": "Economie", "role": "bot", "icone": "\U0001F4B0",
     "desc": "XP, points, titres"},
    {"id": "bot_casino", "nom": "Casino", "role": "bot", "icone": "\U0001F3B2",
     "desc": "Jeux, defis & gains d'XP"},
    {"id": "bot_annonce", "nom": "Crieur", "role": "bot", "icone": "\U0001F4EF",
     "desc": "Diffuse les annonces payantes"},
    {"id": "bot_cine", "nom": "Projectionniste", "role": "bot", "icone": "\U0001F3A5",
     "desc": "Billetterie & signalement P2P du cinema"},
    {"id": "bot_relais", "nom": "Relais", "role": "bot", "icone": "\U0001F6F0",
     "desc": "Bascule vers le 2e serveur si saturation"},
    {"id": "bot_hote", "nom": "Concierge", "role": "bot", "icone": "\U0001F6CE",
     "desc": "Gere les reservations de la Salle Privee"},
]


def _prix(rang):
    return int(round(50 * (1.28 ** rang)))


_NOMS = [
    "Novice", "Apprenti", "Initie", "Disciple", "Voyageur", "Explorateur",
    "Aventurier", "Veteran", "Chevalier", "Gardien", "Sentinelle", "Templier",
    "Champion", "Heros", "Elu", "Maitre", "Grand-Maitre", "Sage", "Oracle",
    "Mystique", "Enchanteur", "Archimage", "Seigneur", "Baron", "Comte", "Duc",
    "Prince", "Roi", "Empereur", "Immortel", "Legende", "Mythe", "Titan",
    "Colosse", "Demi-Dieu", "Divinite", "Ascendant", "Celeste", "Astral",
    "Cosmique", "Eternel", "Infini", "Absolu", "Souverain", "Supreme",
    "Transcendant", "Omniscient", "Createur", "Origine", "HIKARO",
]

TITRES = [{"id": i + 1, "nom": n, "prix": _prix(i)} for i, n in enumerate(_NOMS)]


def level_from_xp(xp):
    lvl, need, total = 1, 100, 0
    while xp >= total + need:
        total += need
        lvl += 1
        need = 100 * lvl
    return lvl
