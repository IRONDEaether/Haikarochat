# -*- coding: utf-8 -*-
"""Bot SQL : persistance des donnees. AUCUNE image stockee (que du texte).

Trois backends, choisis automatiquement (voir config.DATABASE_URL) :
  * "mysql"    : durable, recommande en production (Render a un disque ephemere).
  * "supabase" : si des cles Supabase sont fournies.
  * "local"    : JSON data/local.json, pour le dev uniquement.

Les tickets / reservations restent TEMPORAIRES (en memoire + expiration) et ne
sont jamais ecrits en base : ils vivent le temps de la session.
"""
import json
import os
import threading
import time
from urllib.parse import urlparse, unquote

import config

_LOCK = threading.RLock()
_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "local.json")

# Collections ECRITES en base durable (MySQL/JSON).
# "reservations" est VOLONTAIREMENT absente : les tickets sont temporaires.
_DURABLE = ("users", "messages", "bans", "warns", "mod_requests",
            "annonces", "secrets")


def _parse_mysql_url(url):
    """mysql://user:pass@host:port/base -> dict pour pymysql."""
    u = urlparse(url.replace("mysql+pymysql://", "mysql://"))
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 3306,
        "user": unquote(u.username or "root"),
        "password": unquote(u.password or ""),
        "database": (u.path or "/hikarochat").lstrip("/") or "hikarochat",
        "charset": "utf8mb4",
        "autocommit": True,
    }


class BotSQL:
    def __init__(self):
        self.client = None       # supabase
        self._my = None          # connexion mysql (pymysql)
        self._mycfg = None
        self.mode = "local"
        self._db = {"users": {}, "messages": [], "bans": [], "warns": [],
                    "mod_requests": []}
        self._connect()
        self._load()

    # ---------------------------------------------------------------
    #  Connexion / choix du backend
    # ---------------------------------------------------------------
    def _connect(self):
        if getattr(config, "DATABASE_URL", ""):
            try:
                self._mycfg = _parse_mysql_url(config.DATABASE_URL)
                self._my_connect()
                self._my_init_schema()
                self.mode = "mysql"
                print("[SQL] MySQL connecte (%s/%s)." % (
                    self._mycfg["host"], self._mycfg["database"]))
                return
            except Exception as e:
                print("[SQL] MySQL KO -> fallback. Detail:", e)
        if config.SUPABASE_URL and config.SUPABASE_KEY:
            try:
                from supabase import create_client
                self.client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
                self.mode = "supabase"
                print("[SQL] Supabase connecte.")
                return
            except Exception as e:
                print("[SQL] Supabase KO, mode local:", e)
        print("[SQL] Mode local JSON (aucune base distante configuree).")

    def _my_connect(self):
        import pymysql
        self._my = pymysql.connect(**self._mycfg)

    def _my_cursor(self):
        """Renvoie un curseur en se reconnectant si la connexion a saute."""
        import pymysql
        try:
            self._my.ping(reconnect=True)
        except Exception:
            self._my_connect()
        return self._my.cursor(pymysql.cursors.DictCursor)

    def _my_init_schema(self):
        """Table cle/valeur JSON : simple, modulaire, une ligne par collection."""
        with self._my_cursor() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS hikaro_store ("
                " coll VARCHAR(64) PRIMARY KEY,"
                " data LONGTEXT,"
                " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                "   ON UPDATE CURRENT_TIMESTAMP"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

    def _my_write(self, coll, value):
        payload = json.dumps(value, ensure_ascii=False)
        with self._my_cursor() as c:
            c.execute(
                "INSERT INTO hikaro_store (coll, data) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE data=VALUES(data)",
                (coll, payload))

    def _my_read_all(self):
        with self._my_cursor() as c:
            c.execute("SELECT coll, data FROM hikaro_store")
            out = {}
            for row in c.fetchall():
                try:
                    out[row["coll"]] = json.loads(row["data"])
                except Exception:
                    pass
            return out

    # ---------------------------------------------------------------
    #  Chargement / sauvegarde
    # ---------------------------------------------------------------
    def _load(self):
        if self.mode == "mysql":
            try:
                data = self._my_read_all()
                for k in _DURABLE:
                    if k in data:
                        self._db[k] = data[k]
            except Exception as e:
                print("[SQL] Lecture MySQL KO:", e)
            return
        if os.path.exists(_FILE):
            try:
                with open(_FILE, encoding="utf-8") as f:
                    self._db.update(json.load(f))
            except Exception:
                pass

    def _save(self):
        if self.mode == "mysql":
            try:
                for k in _DURABLE:
                    if k in self._db:
                        self._my_write(k, self._db[k])
            except Exception as e:
                print("[SQL] Ecriture MySQL KO:", e)
            return
        if self.mode != "local":
            return
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(self._db, f, ensure_ascii=False, indent=2)

    # ---- users (JAMAIS d'avatar en base) ----
    def get_user(self, bid):
        with _LOCK:
            if self.mode == "supabase":
                r = self.client.table("users").select("*").eq("browser_id", bid).execute()
                return r.data[0] if r.data else None
            return self._db["users"].get(bid)

    def get_by_pseudo(self, pseudo):
        with _LOCK:
            if self.mode == "supabase":
                r = self.client.table("users").select("*").eq("pseudo", pseudo).execute()
                return r.data[0] if r.data else None
            for u in self._db["users"].values():
                if u.get("pseudo") == pseudo:
                    return u
            return None

    def upsert_user(self, data):
        data.pop("avatar", None)   # securite : aucune image en base
        with _LOCK:
            if self.mode == "supabase":
                self.client.table("users").upsert(data).execute()
            else:
                bid = data["browser_id"]
                cur = self._db["users"].get(bid, {})
                cur.update(data)
                self._db["users"][bid] = cur
                self._save()
            return self.get_user(data["browser_id"])

    def update_user(self, bid, patch):
        patch.pop("avatar", None)
        with _LOCK:
            if self.mode == "supabase":
                self.client.table("users").update(patch).eq("browser_id", bid).execute()
            else:
                u = self._db["users"].get(bid)
                if u:
                    u.update(patch)
                    self._save()
            return self.get_user(bid)

    def list_users(self, role=None):
        with _LOCK:
            if self.mode == "supabase":
                q = self.client.table("users").select("*")
                if role:
                    q = q.eq("role", role)
                return q.execute().data
            return [u for u in self._db["users"].values() if not role or u.get("role") == role]

    # ---- messages (texte uniquement) ----
    def add_message(self, salon, bid, pseudo, contenu, mtype="text"):
        row = {"salon": salon, "browser_id": bid, "pseudo": pseudo,
               "contenu": contenu, "type": mtype, "ts": time.time()}
        with _LOCK:
            if self.mode == "supabase":
                self.client.table("messages").insert(row).execute()
            else:
                self._db["messages"].append(row)
                self._db["messages"] = self._db["messages"][-2000:]
                self._save()

    def history(self, salon, limit=40):
        with _LOCK:
            if self.mode == "supabase":
                r = (self.client.table("messages").select("*").eq("salon", salon)
                     .order("ts", desc=True).limit(limit).execute())
                return list(reversed(r.data))
            return [m for m in self._db["messages"] if m["salon"] == salon][-limit:]

    # ---- bans / warns / mod ----
    def add_ban(self, bid, pseudo, raison, auteur, duree=0):
        expire = 0 if not duree else time.time() + duree
        with _LOCK:
            self._db.setdefault("bans", []).append(
                {"browser_id": bid, "pseudo": pseudo, "raison": raison,
                 "auteur": auteur, "ts": time.time(), "expire": expire})
            if self.mode == "supabase":
                self.client.table("bans").insert(
                    {"browser_id": bid, "pseudo": pseudo, "raison": raison,
                     "auteur": auteur, "expire": expire}).execute()
            else:
                self._save()

    def is_banned(self, bid):
        now = time.time()
        with _LOCK:
            if self.mode == "supabase":
                rows = self.client.table("bans").select("*").eq("browser_id", bid).execute().data
            else:
                rows = [b for b in self._db.get("bans", []) if b["browser_id"] == bid]
            for b in rows:
                if not b.get("expire") or b["expire"] > now:
                    return True
            return False

    def strikes(self, bid):
        """Nombre de sanctions deja infligees a ce membre."""
        with _LOCK:
            return sum(1 for b in self._db.get("bans", []) if b["browser_id"] == bid)

    def add_warn(self, pseudo, raison, auteur):
        with _LOCK:
            self._db.setdefault("warns", []).append(
                {"pseudo": pseudo, "raison": raison, "auteur": auteur, "ts": time.time()})
            if self.mode != "supabase":
                self._save()

    def add_mod_request(self, bid, pseudo, raison):
        row = {"id": int(time.time() * 1000), "browser_id": bid, "pseudo": pseudo,
               "raison": raison, "statut": "attente"}
        with _LOCK:
            self._db.setdefault("mod_requests", []).append(row)
            if self.mode != "supabase":
                self._save()
            return row

    def list_mod_requests(self, statut="attente"):
        with _LOCK:
            return [r for r in self._db.get("mod_requests", []) if r["statut"] == statut]

    def set_mod_request(self, rid, statut):
        with _LOCK:
            for r in self._db.get("mod_requests", []):
                if r["id"] == rid:
                    r["statut"] = statut
            if self.mode != "supabase":
                self._save()

    # ---- annonces (salle Annonce, payantes, ephemeres) ----
    def add_annonce(self, row):
        with _LOCK:
            self._db.setdefault("annonces", []).append(row)
            self._save()
            return row

    def list_annonces(self):
        now = time.time()
        with _LOCK:
            items = [a for a in self._db.get("annonces", []) if a.get("expire", 0) > now]
            self._db["annonces"] = items
            self._save()
            return items

    # ---- 2e boutique : marche des secrets (payant pour reveler, ephemere) ----
    def add_secret(self, row):
        with _LOCK:
            self._db.setdefault("secrets", []).append(row)
            self._save()
            return row

    def list_secrets(self):
        now = time.time()
        with _LOCK:
            items = [s for s in self._db.get("secrets", []) if s.get("expire", 0) > now]
            self._db["secrets"] = items
            self._save()
            return items

    def get_secret(self, sid):
        with _LOCK:
            return next((s for s in self._db.get("secrets", []) if s["id"] == sid), None)

    def reveal_secret(self, sid, bid):
        with _LOCK:
            s = self.get_secret(sid)
            if s and bid not in s.setdefault("buyers", []):
                s["buyers"].append(bid)
                self._save()
            return s

    # ---- reservations (Salle Privee : places limitees, ephemeres) ----
    def _live_reservations(self, salon):
        now = time.time()
        items = [r for r in self._db.get("reservations", [])
                 if r.get("salon") == salon and r.get("expire", 0) > now]
        # purge globale des expirees
        self._db["reservations"] = [r for r in self._db.get("reservations", [])
                                     if r.get("expire", 0) > now]
        return items

    def list_reservations(self, salon):
        with _LOCK:
            items = self._live_reservations(salon)
            self._save()
            return items

    def has_reservation(self, salon, bid):
        with _LOCK:
            return any(r["browser_id"] == bid for r in self._live_reservations(salon))

    def add_reservation(self, salon, bid, pseudo, capacity, duree_h):
        """Retourne (ok, data). data = liste des reservations live ou message."""
        with _LOCK:
            live = self._live_reservations(salon)
            if any(r["browser_id"] == bid for r in live):
                return True, live          # deja reserve
            if len(live) >= capacity:
                return False, "complet"
            row = {"salon": salon, "browser_id": bid, "pseudo": pseudo,
                   "ts": time.time(), "expire": time.time() + duree_h * 3600}
            self._db.setdefault("reservations", []).append(row)
            self._save()
            return True, self._live_reservations(salon)

    def cancel_reservation(self, salon, bid):
        with _LOCK:
            self._db["reservations"] = [r for r in self._db.get("reservations", [])
                                        if not (r.get("salon") == salon and r["browser_id"] == bid)]
            self._save()
            return self._live_reservations(salon)
