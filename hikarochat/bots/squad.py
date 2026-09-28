# -*- coding: utf-8 -*-
"""L'escouade de bots, posee a cote du cerveau.
Haiku (accueil/cerveau), Ambiance, Network (reseau/ban/warn), Economie.
Les bots apparaissent toujours comme des personnes connectees.
"""
import random
import time

import config
from bots import mailer

_AMBIANCE = [
    "On garde une bonne vibe ici :)",
    "Respect & zero spam, l'escouade veille.",
    "Nouveau dans le coin ? Presente-toi !",
    "Besoin d'aide ? File au salon Aide.",
]


class Squad:
    def __init__(self, sql, emit):
        self.sql = sql
        self.emit = emit
        self._act = {}        # anti-flood {bid:[ts]}
        self._warns = {}      # {bid:int}
        self._gain = {}       # anti-farm eco
        self._amb = {}        # cooldown ambiance

    # ---------- bot Haiku : accueil ----------
    def bienvenue(self, salon, user):
        self.emit(salon, "system", {"salon": salon, "bot": "Haiku",
                  "text": "Bienvenue %s ! Niveau %d. Tape /help ou /guide pour la visite guidee." % (user["pseudo"], user.get("level", 1))})
        if salon == "aide":
            self.guide(salon)

    # ---------- Guide : robot de presentation complet de l'appli ----------
    def guide(self, salon):
        """Le robot Haiku presente TOUT le fonctionnement : icones, boutiques, salons."""
        blocs = [
            ("Haiku", "\U0001F44B Salut ! Je suis le robot d'accueil. Voici la visite complete de HIKAROCHAT."),
            ("Haiku", "\U0001F53C BARRE DU HAUT : \u2630 = liste des salons \u00b7 \U0001F465 = qui est en ligne \u00b7 \U0001FA99 = tes points \u00b7 \U0001F514 = notifications \u00b7 \U0001F6D2 = boutique de titres \u00b7 \U0001F381 = marche des secrets \u00b7 \U0001F464 = ton profil (avatar, titre, devenir modo)."),
            ("Economie", "\U0001FA99 POINTS & XP : tu gagnes des points en discutant et en jouant. Les points servent a acheter des titres, poster des annonces, reveler des secrets et debloquer le club secret."),
            ("Economie", "\U0001F6D2 BOUTIQUE DE TITRES : ouvre-la (icone \U0001F6D2) pour acheter un titre affiche a cote de ton pseudo. Plus le titre est prestigieux, plus il coute cher."),
            ("Crieur", "\U0001F381 MARCHE DES SECRETS : une 2e boutique. Tu peux poster un secret payant : les autres paient des points pour le reveler, et tu touches la mise."),
            ("Casino", "\U0001F3B0 SALON CASINO : \U0001F3B2 De, \U0001FA99 Pile ou face (avec mise), \U0001F3B0 Machine a sous, \u2694\uFE0F Defis. On gagne de l'XP et des points."),
            ("Projectionniste", "\U0001F3AC SALON DIVERTISSEMENT : cinema + musique. Prends un billet (\U0001F3AB), puis regarde un film Ensemble (synchronise) ou Perso. Les films/musiques restent 100%% en P2P, jamais stockes."),
            ("Concierge", "\U0001F512 SALLE PRIVEE : places limitees, sur reservation (paye en points). Reserve ta place pour entrer ; ta reservation est temporaire."),
            ("Crieur", "\U0001F4E2 SALON ANNONCES : poste une annonce payante diffusee a tout le chat pendant 1 a 24h."),
            ("Haiku", "\U0001F4AC SALONS : General, Otaku, Manga, Tech, Gaming, Musique, Ecole, Memes, Chill... Choisis-en un a gauche, ou utilise le menu deroulant + le bouton GO pour rejoindre direct. \U0001F576 Un salon SECRET se debloque en tapant le mot 'secret'."),
            ("Network", "\U0001F6E1 SECURITE : je surveille tout. Respect, zero spam, rien d'illegal ni impliquant des mineurs = bannissement. Tape /rules a tout moment."),
            ("Haiku", "\u2705 Voila ! Astuce : tape /guide n'importe ou pour revoir cette visite, et /help pour la liste des commandes. Amuse-toi bien !"),
        ]
        for bot, txt in blocs:
            self.emit(salon, "system", {"salon": salon, "bot": bot, "text": txt})

    # ---------- bot Ambiance ----------
    def ambiance(self, salon, nb):
        now = time.time()
        if nb >= 1 and now - self._amb.get(salon, 0) > 240:
            self._amb[salon] = now
            self.emit(salon, "system", {"salon": salon, "bot": "Ambiance",
                      "text": random.choice(_AMBIANCE)})

    # ---------- bot Network : securite / ban / warn ----------
    def inspect(self, bid, pseudo, texte, salon):
        now = time.time()
        h = [t for t in self._act.get(bid, []) if now - t < 10]
        h.append(now)
        self._act[bid] = h
        if len(h) > 6:
            return self._auto(bid, pseudo, "Flood", salon)
        return True, None

    def _auto(self, bid, pseudo, raison, salon):
        n = self._warns.get(bid, 0) + 1
        self._warns[bid] = n
        if n < 3:
            self.emit(salon, "system", {"salon": salon, "bot": "Network",
                      "text": "@%s avertissement %d/3 : %s" % (pseudo, n, raison)})
            return False, raison
        self.ban(bid, pseudo, raison, "Network")
        return False, raison

    def ban(self, bid, pseudo, raison, auteur):
        strike = self.sql.strikes(bid) + 1
        duree = 0
        for s, d in config.BAN_TIERS:
            if strike <= s:
                duree = d
                break
        else:
            duree = 0
        self.sql.add_ban(bid, pseudo, raison, auteur, duree)
        label = "DEFINITIF" if not duree else self._duree_txt(duree)
        self.emit("__all__", "banned", {"browser_id": bid, "pseudo": pseudo,
                  "raison": raison, "auteur": auteur, "duree": duree})
        self.emit("__all__", "system", {"salon": "__all__", "bot": "Network",
                  "text": "%s banni par %s (%s) \u2014 sanction #%d : %s" % (
                      pseudo, auteur, raison, strike, label)})

    @staticmethod
    def _duree_txt(sec):
        if sec >= 86400:
            return "%dj" % (sec // 86400)
        if sec >= 3600:
            return "%dh" % (sec // 3600)
        return "%dmin" % (sec // 60)

    def warn(self, pseudo, raison, auteur, salon):
        self.sql.add_warn(pseudo, raison, auteur)
        self.emit(salon, "system", {"salon": salon, "bot": "Network",
                  "text": "\u26a0 %s : %s (par %s)" % (pseudo, raison, auteur)})

    # ---------- bot Economie ----------
    def gain(self, bid):
        now = time.time()
        if now - self._gain.get(bid, 0) < 3:
            return None
        self._gain[bid] = now
        u = self.sql.get_user(bid)
        if not u:
            return None
        xp = u.get("xp", 0) + 5
        old = u.get("level", 1)
        lvl = config.level_from_xp(xp)
        pts = u.get("points", 0) + 2 + (50 if lvl > old else 0)
        self.sql.update_user(bid, {"xp": xp, "points": pts, "level": lvl})
        return {"xp": xp, "points": pts, "level": lvl, "level_up": lvl if lvl > old else 0}

    def buy_title(self, bid, tid):
        u = self.sql.get_user(bid)
        t = next((x for x in config.TITRES if x["id"] == tid), None)
        if not u or not t:
            return False, "Titre introuvable"
        if u.get("points", 0) < t["prix"]:
            return False, "Points insuffisants (%d)" % t["prix"]
        self.sql.update_user(bid, {"points": u["points"] - t["prix"], "titre_actif": t["nom"]})
        return True, t

    # ---------- salon secret : deblocage contre des points ----------
    def unlock_secret(self, bid):
        u = self.sql.get_user(bid)
        if not u:
            return False, "Inconnu"
        if u.get("secret_unlocked"):
            return True, "already"
        sec = next((s for s in config.SALONS if s.get("secret")), None)
        if not sec:
            return False, "Pas de salon secret."
        prix = sec.get("prix", 0)
        if u.get("points", 0) < prix:
            return False, "Il te faut %d points pour ouvrir le Club Secret." % prix
        self.sql.update_user(bid, {"points": u["points"] - prix, "secret_unlocked": True})
        return True, sec

    # ---------- bot Casino : jeux & defis pour gagner de l'XP ----------
    def casino(self, bid, pseudo, salon, jeu, arg=None):
        u = self.sql.get_user(bid)
        if not u:
            return
        xp = u.get("xp", 0)
        pts = u.get("points", 0)
        txt = None
        if jeu == "de":
            r = random.randint(1, 6)
            g = r * 3
            xp += g
            txt = "\U0001F3B2 %s lance le d\u00e9 : %d \u2192 +%d XP !" % (pseudo, r, g)
        elif jeu == "pileface":
            mise = max(0, min(int(arg or 0), pts))
            win = random.random() < 0.5
            pts += mise if win else -mise
            xp += 10 if win else 0
            txt = "\U0001FA99 %s parie %d pts : %s !" % (
                pseudo, mise, "GAGN\u00c9 (+%d pts +10 XP)" % mise if win else "perdu")
        elif jeu == "machine":
            sym = [random.choice(["\U0001F352", "\U0001F514", "\u2b50", "\U0001F48E"]) for _ in range(3)]
            jack = len(set(sym)) == 1
            g = 100 if jack else (20 if len(set(sym)) == 2 else 0)
            xp += g
            txt = "\U0001F3B0 %s | %s | %s" % (sym[0], sym[1], sym[2]) + (
                " \u2014 JACKPOT +100 XP !" if jack else (" +%d XP" % g if g else " \u2014 rien"))
        elif jeu == "defi":
            xp += 15
            txt = "\u2694\ufe0f %s lance un d\u00e9fi : %s (+15 XP)" % (pseudo, arg or "qui releve ?")
        else:
            self.emit(salon, "system", {"salon": salon, "bot": "Casino",
                      "text": "Jeux : /de  /pileface <mise>  /machine  /defi <texte>"})
            return
        lvl = config.level_from_xp(xp)
        self.sql.update_user(bid, {"xp": xp, "points": max(0, pts), "level": lvl})
        self.emit(salon, "system", {"salon": salon, "bot": "Casino", "text": txt})
        self.emit(bid, "economy", {"xp": xp, "points": max(0, pts), "level": lvl}, True)

    # ---------- bot Crieur : annonces payantes ephemeres ----------
    def post_annonce(self, user, texte, heures):
        u = self.sql.get_user(user["browser_id"])
        if not u:
            return False, "Inconnu"
        if u.get("points", 0) < config.ANNONCE_PRIX:
            return False, "Il faut %d pts pour poster une annonce." % config.ANNONCE_PRIX
        h = max(config.SECRET_MIN_H, min(int(heures or 1), config.SECRET_MAX_H))
        self.sql.update_user(user["browser_id"], {"points": u["points"] - config.ANNONCE_PRIX})
        row = {"id": int(time.time() * 1000), "auteur": user["pseudo"], "texte": texte[:500],
               "ts": time.time(), "expire": time.time() + h * 3600}
        self.sql.add_annonce(row)
        self.emit("__all__", "annonce", row)
        self.emit("__all__", "system", {"salon": "__all__", "bot": "Crieur",
                  "text": "\U0001F4E2 Nouvelle annonce de %s (valable %dh)" % (user["pseudo"], h)})
        return True, row

    # ---------- 2e boutique : marche des secrets (payant pour reveler) ----------
    def post_secret(self, user, titre, desc, contenu, prix, heures):
        h = max(config.SECRET_MIN_H, min(int(heures or 1), config.SECRET_MAX_H))
        row = {"id": int(time.time() * 1000), "auteur": user["pseudo"],
               "auteur_bid": user["browser_id"], "titre": titre[:80], "desc": desc[:200],
               "contenu": contenu[:1000], "prix": max(0, int(prix)), "buyers": [],
               "ts": time.time(), "expire": time.time() + h * 3600}
        self.sql.add_secret(row)
        pub = {k: row[k] for k in ("id", "auteur", "titre", "desc", "prix", "expire")}
        self.emit("__all__", "secret_new", pub)
        return True, pub

    def buy_secret(self, bid, sid):
        s = self.sql.get_secret(sid)
        u = self.sql.get_user(bid)
        if not s or not u:
            return False, "Secret introuvable"
        if s["expire"] < time.time():
            return False, "Secret expire"
        if bid == s.get("auteur_bid") or bid in s.get("buyers", []):
            return True, s["contenu"]
        if u.get("points", 0) < s["prix"]:
            return False, "Points insuffisants (%d)" % s["prix"]
        self.sql.update_user(bid, {"points": u["points"] - s["prix"]})
        self.sql.reveal_secret(sid, bid)
        return True, s["contenu"]

    # ---------- bot Projectionniste : billet cinema (XP) ----------
    def cinema_ticket(self, bid, pseudo, salon):
        u = self.sql.get_user(bid)
        if not u:
            return False, "Inconnu"
        if u.get("xp", 0) < config.CINEMA_TICKET_XP:
            return False, "Il te faut %d XP pour un billet." % config.CINEMA_TICKET_XP
        self.sql.update_user(bid, {"xp": u["xp"] - config.CINEMA_TICKET_XP})
        self.emit(salon, "system", {"salon": salon, "bot": "Projectionniste",
                  "text": "\U0001F3AB %s a pris son billet (-%d XP). Bon film !" % (
                      pseudo, config.CINEMA_TICKET_XP)})
        return True, u["xp"] - config.CINEMA_TICKET_XP

    def _pub_resa(self, salon, cap):
        live = self.sql.list_reservations(salon)
        return {"salon": salon, "count": len(live), "capacity": cap,
                "membres": [r["pseudo"] for r in live]}

    # ---------- bot Concierge : reservations de la Salle Privee ----------
    def reserve_place(self, user, salon):
        cfg = next((s for s in config.SALONS if s["id"] == salon and s.get("reservable")), None)
        if not cfg:
            return False, "Salon non reservable."
        bid = user["browser_id"]
        cap = cfg.get("capacity", 0)
        u = self.sql.get_user(bid)
        if not u:
            return False, "Inconnu"
        if self.sql.has_reservation(salon, bid):
            return True, self._pub_resa(salon, cap)
        prix = cfg.get("prix", 0)
        if u.get("points", 0) < prix:
            return False, "Il te faut %d points pour reserver une place." % prix
        ok, res = self.sql.add_reservation(salon, bid, user["pseudo"], cap,
                                           config.SALLE_RESERVATION_H)
        if not ok:
            return False, "Salle complete (%d/%d). Reessaie plus tard." % (cap, cap)
        if prix:
            self.sql.update_user(bid, {"points": u["points"] - prix})
        pub = self._pub_resa(salon, cap)
        self.emit("__all__", "reservation_update", pub)
        self.emit("__all__", "system", {"salon": "__all__", "bot": "Concierge",
                  "text": "\U0001F512 %s a reserve une place (Salle Privee : %d/%d)." % (
                      user["pseudo"], pub["count"], cap)})
        return True, pub

    def cancel_place(self, user, salon):
        cfg = next((s for s in config.SALONS if s["id"] == salon and s.get("reservable")), None)
        if not cfg:
            return False, "Salon non reservable."
        self.sql.cancel_reservation(salon, user["browser_id"])
        pub = self._pub_resa(salon, cfg.get("capacity", 0))
        self.emit("__all__", "reservation_update", pub)
        return True, pub

    # ---------- moderation : demande + approbation email ----------
    def demande_mod(self, user, raison, base_url=""):
        self.sql.add_mod_request(user["browser_id"], user["pseudo"], raison)
        lien = base_url.rstrip("/") + "/panel?code=" + config.ADMIN_CODE
        corps = ("Demande moderateur HIKAROCHAT\n\nPseudo: %s\nRaison: %s\n\n"
                 "Approuver/refuser ici:\n%s\n\n-- Bot Haiku") % (
                     user["pseudo"], raison, lien)
        mailer.send("[HIKAROCHAT] Demande modo - %s" % user["pseudo"], corps)

    def approve(self, rid, bid):
        self.sql.set_mod_request(rid, "approuve")
        self.sql.update_user(bid, {"role": "modo"})
        u = self.sql.get_user(bid)
        if u:
            self.emit("__all__", "role_change", {"browser_id": bid, "pseudo": u["pseudo"], "role": "modo"})

    def refuse(self, rid):
        self.sql.set_mod_request(rid, "refuse")

    def kick_mod(self, bid):
        self.sql.update_user(bid, {"role": "user"})
        u = self.sql.get_user(bid)
        if u:
            self.emit("__all__", "role_change", {"browser_id": bid, "pseudo": u["pseudo"], "role": "user"})
