# -*- coding: utf-8 -*-
"""HIKAROCHAT - cerveau.py (v2)
Flask + Flask-SocketIO. L'escouade de bots est posee a cote (bots/squad.py)
et apparait toujours comme connectee. Aucune image en base : photos en P2P /
localStorage, diffusees par socket aux membres connectes.
"""
# --- Temps reel performant : eventlet fournit un VRAI WebSocket (ideal Render).
#     Le monkey_patch DOIT etre fait avant tout autre import reseau.
try:
    import eventlet
    eventlet.monkey_patch()
    _ASYNC_MODE = "eventlet"
except Exception:            # pas d'eventlet en local -> repli threading
    _ASYNC_MODE = "threading"

import hashlib
import time

from flask import Flask, render_template, request, jsonify, session
from flask_socketio import SocketIO, join_room, leave_room, emit

import config
from bots.sql import BotSQL
from bots.squad import Squad

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
socketio = SocketIO(app, async_mode=_ASYNC_MODE, cors_allowed_origins="*",
                    ping_timeout=60, ping_interval=25,
                    max_http_buffer_size=200 * 1024 * 1024,
                    http_compression=True, compression_threshold=512,
                    message_queue=(config.MESSAGE_QUEUE or None))

# --- PERF: reponse gelee pour les fichiers statiques + micro-cache d'assets ---
try:
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 30
except Exception:
    pass


@app.after_request
def _perf_headers(resp):
    """PERF: compression des reponses texte + cache navigateur des assets statiques."""
    try:
        p = request.path or ""
        if p.startswith("/static/"):
            resp.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
    except Exception:
        pass
    return resp

sql = BotSQL()


def _emit(target, event, payload, sid=False):
    if sid:
        socketio.emit(event, payload, to=target)
    elif target == "__all__":
        socketio.emit(event, payload)
    else:
        socketio.emit(event, payload, to=target)


squad = Squad(sql, _emit)

# presence en memoire : sid -> {bid, pseudo, role, salon, avatar}
SID = {}

# Anti-flood en memoire : sid -> {"msg": [ts...], "media": dernier_ts}
FLOOD = {}


# --- PERSISTANCE v2.4 : buffer circulaire des messages recents par salon.
#     Permet de re-afficher le fil (texte + medias) quand on se reconnecte.
RECENT = {}
RECENT_MAX = 30

# --- SECURITE/PERF : plafonds de taille (en octets) des medias passant par le serveur.
#     Au-dela, on refuse -> le client bascule sur l'envoi direct P2P (WebRTC).
MEDIA_LIMITS = {"image": 2 * 1024 * 1024, "voice": 3 * 1024 * 1024,
                "video": 16 * 1024 * 1024, "file": 10 * 1024 * 1024}


def _too_big(data, kind):
    try:
        return len(data or "") > MEDIA_LIMITS.get(kind, 2 * 1024 * 1024)
    except Exception:
        return False


def _recent_push(salon, msg):
    """Memorise un message/mediа recent pour le rejouer a la reconnexion."""
    if not salon:
        return
    try:
        buf = RECENT.setdefault(salon, [])
        buf.append(msg)
        if len(buf) > RECENT_MAX:
            del buf[:len(buf) - RECENT_MAX]
    except Exception:
        pass


def _recent_since(salon, since):
    """Messages recents postes apres 'since' (reconnexion / reprise de session)."""
    try:
        since = float(since or 0)
    except Exception:
        since = 0
    out = [m for m in RECENT.get(salon, []) if float(m.get("ts", 0)) > since]
    return out[-RECENT_MAX:]


def _flood_ok(sid, kind):
    """Garde-fou anti-spam/robustesse : limite le debit des messages/medias."""
    now = time.time()
    rec = FLOOD.setdefault(sid, {"msg": [], "media": 0.0})
    if kind == "media":
        if now - rec["media"] < 0.8:
            return False
        rec["media"] = now
        return True
    rec["msg"] = [t for t in rec["msg"] if now - t < 4.0]
    if len(rec["msg"]) >= 8:
        return False
    rec["msg"].append(now)
    return True


def _salon_cfg(salon):
    return next((s for s in config.SALONS if s["id"] == salon), {})


def _can_write(me, salon):
    """Salons en lecture seule (admin_write) : seuls admin/owner y publient."""
    cfg = _salon_cfg(salon)
    if cfg.get("admin_write") and me.get("role") not in ("admin", "owner"):
        return False
    return True

# Notes de l'appli (etoiles 1-5) donnees par les utilisateurs, en memoire.
RATINGS = []


def _rating_stats():
    n = len(RATINGS)
    avg = round(sum(RATINGS) / n, 1) if n else 0
    return {"avg": avg, "count": n}


def _sig(bid):
    return hashlib.sha256((bid + config.SECRET_KEY).encode()).hexdigest()


def _user_code(bid):
    """Numero perso stable (8 chiffres) derive du browser_id.
    A distribuer aux autres pour ouvrir une discussion privee."""
    h = hashlib.sha256(("code:" + bid + config.SECRET_KEY).encode()).hexdigest()
    n = int(h[:12], 16) % 100000000
    return "%08d" % n


def _fmt_code(code):
    c = "".join(ch for ch in str(code) if ch.isdigit())
    return c[:4] + "-" + c[4:] if len(c) == 8 else c


def _find_by_code(code):
    """Retrouve l'utilisateur dont le numero perso correspond."""
    want = "".join(ch for ch in str(code) if ch.isdigit())
    if len(want) != 8:
        return None
    for u in sql.list_users():
        if _user_code(u["browser_id"]) == want:
            return u
    return None


def _salons_for(user):
    role, lvl = user.get("role", "user"), user.get("level", 1)
    out = []
    for s in config.SALONS:
        if s.get("admin") and role not in ("admin", "owner"):
            continue
        if s.get("level") and lvl < s["level"]:
            continue
        if s.get("secret") and not user.get("secret_unlocked"):
            continue
        if s.get("secret"):
            s = dict(s, nom=s.get("real_nom", s["nom"]), desc=s.get("real_desc", ""))
        out.append(s)
    return out


def _online(salon):
    """Liste des membres d'un salon : bots (toujours) + humains connectes.
    Dedoublonnage par browser_id pour eviter les fantomes de reconnexion."""
    membres = [{"pseudo": b["nom"], "role": "bot", "icone": b["icone"],
                "desc": b["desc"], "avatar": ""} for b in config.BOTS]
    seen = set()
    for info in SID.values():
        if info.get("salon") == salon and info.get("bid") not in seen:
            seen.add(info.get("bid"))
            membres.append({"pseudo": info["pseudo"], "role": info["role"],
                            "avatar": info.get("avatar", ""), "desc": info.get("desc", "")})
    return membres


def _push_online(salon):
    m = _online(salon)
    socketio.emit("online", {"salon": salon, "membres": m, "count": len(m)}, to=salon)
    _push_counts()


def _salon_counts():
    """Nombre d'humains distincts connectes par salon (pour l'accueil)."""
    seen, c = set(), {}
    for info in SID.values():
        sal = info.get("salon")
        if not sal:
            continue
        key = (sal, info.get("bid"))
        if key in seen:
            continue
        seen.add(key)
        c[sal] = c.get(sal, 0) + 1
    return c


def _push_counts():
    try:
        socketio.emit("salon_counts", {"counts": _salon_counts()})
    except Exception:
        pass


def _annuaire(me_bid=None):
    """Annuaire des membres connus (en ligne OU hors ligne) pour la recherche.
    Permet de retrouver et d'ecrire a quelqu'un meme deconnecte."""
    online_bids = {info.get("bid") for info in SID.values()}
    out = []
    try:
        users = sql.list_users()
    except Exception:
        users = []
    for u in users:
        bid = u.get("browser_id")
        if not bid or not u.get("pseudo"):
            continue
        out.append({"pseudo": u.get("pseudo"), "avatar": u.get("avatar", ""),
                    "role": u.get("role", "user"), "online": bid in online_bids,
                    "code": _fmt_code(_user_code(bid)), "me": bid == me_bid,
                    "last_seen": u.get("last_seen", 0)})
    out.sort(key=lambda x: (not x["online"], (x["pseudo"] or "").lower()))
    return out[:300]


# presence join/leave : anti-spam par (bid, salon)
PRES_LAST = {}


def _bid_in_salon(bid, salon, skip_sid=None):
    """Nombre de sessions (sid) du meme utilisateur presentes dans le salon."""
    return sum(1 for s, info in SID.items()
               if info.get("salon") == salon and info.get("bid") == bid and s != skip_sid)


def _sids_by_pseudo(pseudo, salon):
    """Sessions (sid) d'un pseudo dans un salon donne (routage signaling P2P)."""
    return [s for s, info in SID.items()
            if info.get("pseudo") == pseudo and info.get("salon") == salon]


def _presence(salon, pseudo, kind, bid=None):
    """Annonce 'X a rejoint / quitte le salon' aux membres, avec anti-spam."""
    if not salon:
        return
    if bid is not None:
        key = (bid, salon)
        now = time.time()
        prev = PRES_LAST.get(key)
        if prev and prev[0] == kind and now - prev[1] < 15:
            return
        PRES_LAST[key] = (kind, now)
    socketio.emit("presence", {"salon": salon, "pseudo": pseudo,
                  "event": kind, "ts": time.time()}, to=salon)


def _check_saturation():
    """Le Bot Relais n'active la bascule 2e serveur QUE si l'instance est saturee."""
    if len(SID) >= config.MAX_CLIENTS:
        socketio.emit("saturated", {"backup_url": config.BACKUP_URL, "instance": config.INSTANCE_ID})
        socketio.emit("system", {"salon": "__all__", "bot": "Relais",
                      "text": "\u26a0 Serveur %s sature (%d). Bascule vers le 2e serveur\u2026"
                      % (config.INSTANCE_ID, len(SID))})


# ===================== ROUTES =====================
@app.route("/")
def loading():
    return render_template("loading.html", support=config.SUPPORT_EMAIL,
                           rating=_rating_stats())


@app.route("/register")
def register_page():
    return render_template("register.html", support=config.SUPPORT_EMAIL)


@app.route("/owner")
def owner_page():
    return render_template("owner.html", support=config.SUPPORT_EMAIL)


@app.route("/chat")
def chat_page():
    return render_template("chat.html", support=config.SUPPORT_EMAIL)


@app.route("/panel")
def panel():
    if not session.get("owner") and request.args.get("code") != config.ADMIN_CODE:
        return "Acces refuse.", 403
    return render_template("panel.html", support=config.SUPPORT_EMAIL,
                           demandes=sql.list_mod_requests(), mods=sql.list_users("modo"))


@app.route("/config")
def api_config():
    return jsonify({"salons": config.SALONS, "titres": config.TITRES,
                    "bots": config.BOTS, "support": config.SUPPORT_EMAIL,
                    "version": config.VERSION})


@app.route("/api/rate", methods=["POST"])
def api_rate():
    d = request.get_json(force=True) or {}
    try:
        note = int(d.get("note", 0))
    except (ValueError, TypeError):
        note = 0
    if not (1 <= note <= 5):
        return jsonify({"ok": False, "error": "Note 1-5"}), 400
    RATINGS.append(note)
    if len(RATINGS) > 5000:
        del RATINGS[0]
    return jsonify({"ok": True, **_rating_stats()})


@app.route("/api/rating")
def api_rating():
    return jsonify(_rating_stats())


@app.route("/health")
def health():
    """Sonde pour l'equilibreur : indique quelle instance repond et son etat."""
    return jsonify({"ok": True, "instance": config.INSTANCE_ID,
                    "version": config.VERSION, "db": sql.mode,
                    "clients": len(SID), "queue": bool(config.MESSAGE_QUEUE)})


@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.get_json(force=True)
    pseudo = (d.get("pseudo") or "").strip()[:24]
    bid = (d.get("browser_id") or "").strip()
    if not pseudo or not bid:
        return jsonify({"ok": False, "error": "Pseudo et empreinte requis"}), 400
    try:
        age = int(d.get("age") or 0)
    except ValueError:
        age = 0
    if age < config.MIN_AGE:
        return jsonify({"ok": False, "error": "Age minimum %d" % config.MIN_AGE}), 400
    ex = sql.get_by_pseudo(pseudo)
    if ex and ex["browser_id"] != bid:
        return jsonify({"ok": False, "error": "Pseudo deja pris"}), 409
    user = sql.upsert_user({"browser_id": bid, "ip_address": request.remote_addr,
                            "signature_hash": _sig(bid), "pseudo": pseudo, "age": age,
                            "sexe": (d.get("sexe") or "")[:10], "pays": (d.get("pays") or "")[:40],
                            "role": "user"})
    return jsonify({"ok": True, "user": user, "signature": _sig(bid)})


@app.route("/api/register_owner", methods=["POST"])
def api_register_owner():
    """Inscription proprietaire/admin separee : pseudo + mot de passe + code secret."""
    d = request.get_json(force=True)
    if (d.get("code") or "") != config.OWNER_CODE:
        return jsonify({"ok": False, "error": "Code proprietaire invalide"}), 403
    pseudo = (d.get("pseudo") or "").strip()[:24]
    bid = (d.get("browser_id") or "").strip()
    pwd = (d.get("password") or "")
    if not pseudo or not bid or len(pwd) < 4:
        return jsonify({"ok": False, "error": "Pseudo, empreinte et mot de passe (4+) requis"}), 400
    ex = sql.get_by_pseudo(pseudo)
    if ex and ex["browser_id"] != bid:
        return jsonify({"ok": False, "error": "Pseudo deja pris"}), 409
    user = sql.upsert_user({"browser_id": bid, "ip_address": request.remote_addr,
                            "signature_hash": _sig(bid), "pseudo": pseudo, "age": 18,
                            "pass_hash": hashlib.sha256(pwd.encode()).hexdigest(),
                            "role": "owner"})
    session["owner"] = True
    return jsonify({"ok": True, "user": user, "signature": _sig(bid)})


# ===================== SOCKET =====================
@socketio.on_error_default
def _socket_error(e):
    """Robustesse : une exception dans un handler ne doit jamais tuer la session."""
    try:
        print("[SOCKET ERR]", repr(e))
    except Exception:
        pass


def _me():
    return SID.get(request.sid)


def _secrets_pub(bid):
    """Ne jamais divulguer le 'contenu' d'un secret non achete."""
    out = []
    for s in sql.list_secrets():
        pub = {k: s.get(k) for k in ("id", "auteur", "titre", "desc", "prix", "expire")}
        if bid == s.get("auteur_bid") or bid in s.get("buyers", []):
            pub["contenu"] = s.get("contenu")
        out.append(pub)
    return out


def _resa_state():
    """Etat des reservations pour chaque salon reservable."""
    out = {}
    for s in config.SALONS:
        if s.get("reservable"):
            live = sql.list_reservations(s["id"])
            out[s["id"]] = {"count": len(live), "capacity": s.get("capacity", 0),
                            "membres": [r["pseudo"] for r in live]}
    return out


@socketio.on("auth")
def on_auth(d):
    bid = d.get("browser_id", "")
    user = sql.get_user(bid)
    if not user:
        emit("auth_error", {"error": "Inconnu, re-inscription"}); return
    if sql.is_banned(bid):
        emit("auth_error", {"error": "Tu es banni"}); return
    if d.get("signature") != user.get("signature_hash"):
        emit("auth_error", {"error": "Signature invalide"}); return
    # --- Limite GLOBALE d'utilisateurs simultanes ---
    connectes = {info["bid"] for info in SID.values()}
    if bid not in connectes and len(connectes) >= config.APP_CAPACITY:
        emit("app_full", {"max": config.APP_CAPACITY,
             "text": "L'appli est pleine (%d personnes en ligne). Reessaie plus tard." % config.APP_CAPACITY})
        return
    # --- Anti-doublon : purge les anciennes sessions du MEME utilisateur ---
    #     (sur iOS/mobile, une reconnexion cree un nouveau sid avant que
    #      l'ancien 'disconnect' n'arrive -> sinon on apparait en double).
    stale = [s for s, info in SID.items() if info.get("bid") == bid and s != request.sid]
    stale_salons = set()
    for s in stale:
        old = SID.pop(s, None)
        FLOOD.pop(s, None)
        if old and old.get("salon"):
            stale_salons.add(old["salon"])
        try:
            socketio.server.disconnect(s)
        except Exception:
            pass
    SID[request.sid] = {"bid": bid, "pseudo": user["pseudo"], "role": user.get("role", "user"),
                        "salon": None, "avatar": d.get("avatar", "")}
    for sal in stale_salons:
        _push_online(sal)
    try:
        sql.update_user(bid, {"last_seen": time.time()})
    except Exception:
        pass
    emit("auth_ok", {"user": user, "salons": _salons_for(user),
                     "mon_code": _fmt_code(_user_code(bid)),
                     "annuaire": _annuaire(bid),
                     "titres": config.TITRES, "bots": config.BOTS,
                     "gifts": GIFTS,
                     "annonces": sql.list_annonces(), "secrets": _secrets_pub(bid),
                     "reservations": _resa_state(),
                     "settings": {"annonce_prix": config.ANNONCE_PRIX,
                                  "ticket_xp": config.CINEMA_TICKET_XP,
                                  "secret_max_h": config.SECRET_MAX_H,
                                  "backup_url": config.BACKUP_URL,
                                  "cinema_secure": config.CINEMA_SECURE,
                                  "salle_capacity": config.SALLE_CAPACITY,
                                  "salle_prix": config.SALLE_PRIX,
                                  "salle_h": config.SALLE_RESERVATION_H}})
    _check_saturation()
    # --- Livraison des messages prives recus HORS LIGNE (store-and-forward) ---
    try:
        pend = sql.pop_pending_pm(bid)
    except Exception:
        pend = []
    for it in pend:
        try:
            emit("pm", {"from": it.get("from", "?"), "to": user["pseudo"],
                        "text": it.get("text", ""), "ts": it.get("ts", time.time()),
                        "offline": True})
        except Exception:
            pass
    if pend:
        emit("pm_delivered", {"count": len(pend)})


@socketio.on("join")
def on_join(d):
    me = _me()
    if not me:
        return
    salon = d.get("salon")
    if not any(s["id"] == salon for s in config.SALONS):
        return
    user = sql.get_user(me["bid"])
    if salon not in [s["id"] for s in _salons_for(user)]:
        emit("denied", {"raison": "Acces refuse"}); return
    cfg = next((s for s in config.SALONS if s["id"] == salon), {})
    if cfg.get("reservable") and not sql.has_reservation(salon, me["bid"]):
        emit("denied", {"raison": "Reserve ta place d'abord (Salle Privee)."}); return
    if me["salon"] and me["salon"] != salon:
        leave_room(me["salon"]); old = me["salon"]; me["salon"] = None; _push_online(old)
        if _bid_in_salon(me["bid"], old) == 0:
            _presence(old, me["pseudo"], "leave", bid=me["bid"])
    already = _bid_in_salon(me["bid"], salon, skip_sid=request.sid) > 0
    join_room(salon)
    me["salon"] = salon
    emit("history", {"salon": salon, "messages": sql.history(salon)
                      + _recent_since(salon, 0)})
    me["last_replay"] = time.time()
    squad.bienvenue(salon, user)
    _push_online(salon)
    if not already:
        _presence(salon, me["pseudo"], "join", bid=me["bid"])
    if me.get("avatar"):
        socketio.emit("avatar", {"pseudo": me["pseudo"], "avatar": me["avatar"]}, to=salon)


@socketio.on("sync_since")
def on_sync_since(d):
    """Reconnexion : renvoie tout ce qui a ete poste depuis 'ts' (texte + medias)."""
    me = _me()
    if not me:
        return
    salon = me.get("salon") or d.get("salon")
    if not salon:
        return
    try:
        since = float(d.get("ts") or 0)
    except Exception:
        since = 0
    emit("sync_fill", {"salon": salon, "messages": _recent_since(salon, since)})


@socketio.on("leave")
def on_leave(d=None):
    me = _me()
    if not me or not me["salon"]:
        return
    s = me["salon"]; leave_room(s); me["salon"] = None; _push_online(s)
    if _bid_in_salon(me["bid"], s) == 0:
        _presence(s, me["pseudo"], "leave", bid=me["bid"])


@socketio.on("typing")
def on_typing(d):
    """Indicateur '... est en train d'ecrire' diffuse au salon courant."""
    me = _me()
    if not me or not me["salon"]:
        return
    socketio.emit("typing", {"salon": me["salon"], "pseudo": me["pseudo"],
                  "on": bool(d.get("on"))}, to=me["salon"])


@socketio.on("message")
def on_message(d):
    me = _me()
    if not me or not me["salon"]:
        return
    salon = me["salon"]
    txt = (d.get("text") or "").strip()[:2000]
    if not txt:
        return
    if txt.lower() in ("secret", "/secret", "/club") and _unlock_secret(me, salon):
        return
    if txt.startswith("/") and _command(me, salon, txt):
        return
    if not _flood_ok(request.sid, "msg"):
        emit("system", {"salon": salon, "bot": "Network", "text": "Doucement, tu ecris trop vite."})
        return
    if not _can_write(me, salon):
        emit("system", {"salon": salon, "bot": "Network",
             "text": "Ce salon est en lecture seule : seuls les admins peuvent y publier."})
        return
    ok, _ = squad.inspect(me["bid"], me["pseudo"], txt, salon)
    if not ok:
        return
    user = sql.get_user(me["bid"])
    anon = bool(d.get("anon"))
    if anon:
        payload = {"salon": salon, "pseudo": "\U0001F464 Anonyme", "titre": None,
                   "premium": False, "anon": True, "avatar": "", "text": txt, "ts": time.time()}
        sql.add_message(salon, me["bid"], "\U0001F464 Anonyme", txt)
    else:
        pref = "@" if me["role"] in ("modo", "admin", "owner") else ""
        payload = {"salon": salon, "pseudo": pref + me["pseudo"], "titre": user.get("titre_actif"),
                   "premium": bool(user.get("premium")),
                   "avatar": me.get("avatar", ""), "text": txt, "ts": time.time()}
        sql.add_message(salon, me["bid"], me["pseudo"], txt)
    _recent_push(salon, payload)
    socketio.emit("message", payload, to=salon)
    g = squad.gain(me["bid"])
    if g:
        emit("economy", g)
    squad.ambiance(salon, len([x for x in SID.values() if x.get("salon") == salon]))


def _unlock_secret(me, salon):
    ok, res = squad.unlock_secret(me["bid"])
    if ok and res == "already":
        emit("system", {"salon": salon, "bot": "Haiku", "text": "Le Club Secret t'est deja ouvert."})
    elif ok:
        user = sql.get_user(me["bid"])
        emit("economy", {"points": user.get("points", 0)})
        emit("salons_update", {"salons": _salons_for(user)})
        emit("system", {"salon": salon, "bot": "Network",
             "text": "Acces au Club Secret debloque ! Il apparait dans ta liste de salons."})
    else:
        emit("system", {"salon": salon, "bot": "Economie", "text": res})
    return True


def _command(me, salon, txt):
    p = txt.split()
    c = p[0].lower()
    is_mod = me["role"] in ("modo", "admin", "owner")
    if c == "/help":
        cmds = "/help /guide /me /roll /rules /online /bang <pseudo>"
        if is_mod:
            cmds += " | MODO: /ban <pseudo> <raison> /warn <pseudo> <raison>"
        cmds += " | CASINO: /de /pileface <mise> /machine /defi <txt> | CINE: /ticket"
        emit("system", {"salon": salon, "bot": "Haiku", "text": "Commandes: " + cmds})
    elif c == "/guide":
        squad.guide(salon)
    elif c == "/me":
        socketio.emit("system", {"salon": salon, "bot": "Ambiance",
                      "text": "* %s %s" % (me["pseudo"], " ".join(p[1:]))}, to=salon)
    elif c == "/roll":
        import random
        socketio.emit("system", {"salon": salon, "bot": "Ambiance",
                      "text": "%s -> %d" % (me["pseudo"], random.randint(1, 100))}, to=salon)
    elif c == "/rules":
        emit("system", {"salon": salon, "bot": "Network",
             "text": "Respect, zero spam, rien d'illegal. L'escouade veille."})
    elif c == "/online":
        m = _online(salon)
        emit("system", {"salon": salon, "bot": "Haiku",
             "text": "En ligne (%d): %s" % (len(m), ", ".join(x["pseudo"] for x in m))})
    elif c == "/bang":
        if len(p) < 2:
            emit("system", {"salon": salon, "bot": "Haiku", "text": "Usage: /bang <pseudo>"})
            return True
        cible = sql.get_by_pseudo(p[1])
        if not cible:
            emit("system", {"salon": salon, "bot": "Haiku", "text": "Inconnu au bataillon : %s" % p[1]})
            return True
        if any(x["pseudo"] == cible["pseudo"] for x in SID.values()):
            emit("system", {"salon": salon, "bot": "Haiku",
                 "text": "%s est connecte maintenant \U0001F7E2" % cible["pseudo"]})
        else:
            ls = cible.get("last_seen")
            if not ls:
                emit("system", {"salon": salon, "bot": "Haiku",
                     "text": "On n'a jamais vu %s par ici." % cible["pseudo"]})
            else:
                diff = max(0, int(time.time() - float(ls)))
                j = diff // 86400
                h = (diff % 86400) // 3600
                mn = (diff % 3600) // 60
                if j:
                    dur = "%dj %dh" % (j, h)
                elif h:
                    dur = "%dh %dmin" % (h, mn)
                else:
                    dur = "%dmin" % mn
                emit("system", {"salon": salon, "bot": "Haiku",
                     "text": "%s est absent depuis %s \U0001F480" % (cible["pseudo"], dur)})
    elif c == "/ban" and is_mod and len(p) >= 2:
        cible = sql.get_by_pseudo(p[1])
        if not cible or cible.get("role") == "owner":
            emit("system", {"salon": salon, "bot": "Network", "text": "Cible invalide."})
        else:
            squad.ban(cible["browser_id"], cible["pseudo"], " ".join(p[2:]) or "Sanction", "@" + me["pseudo"])
    elif c == "/warn" and is_mod and len(p) >= 2:
        squad.warn(p[1], " ".join(p[2:]) or "Avertissement", "@" + me["pseudo"], salon)
    elif c in ("/de", "/pileface", "/machine", "/defi"):
        jeu = {"/de": "de", "/pileface": "pileface", "/machine": "machine", "/defi": "defi"}[c]
        squad.casino(me["bid"], me["pseudo"], salon, jeu, " ".join(p[1:]))
    elif c == "/ticket":
        ok, res = squad.cinema_ticket(me["bid"], me["pseudo"], salon)
        if ok:
            me["cine_ticket"] = True
            emit("economy", {"xp": res})
            emit("cine_ticket_ok", {"xp": res})
        else:
            emit("system", {"salon": salon, "bot": "Projectionniste", "text": res})
    else:
        return False
    return True


@socketio.on("image")
def on_image(d):
    me = _me()
    if not me or not me["salon"]:
        return
    data = d.get("data", "")
    if not data:
        return
    if not _flood_ok(request.sid, "media") or not _can_write(me, me["salon"]):
        return
    if _too_big(data, "image"):
        emit("media_too_big", {"kind": "image"}); return
    # pas de limite : on diffuse aux AUTRES, l'expediteur l'affiche en local
    payload = {"salon": me["salon"], "pseudo": me["pseudo"],
               "avatar": me.get("avatar", ""), "image": data, "ts": time.time()}
    _recent_push(me["salon"], payload)
    socketio.emit("image", payload, to=me["salon"], include_self=False)


@socketio.on("voice")
def on_voice(d):
    """Message vocal : diffuse en direct au salon (ephemere, jamais en base)."""
    me = _me()
    if not me or not me["salon"]:
        return
    data = d.get("data", "")
    if not data:
        return
    if not _flood_ok(request.sid, "media") or not _can_write(me, me["salon"]):
        return
    if _too_big(data, "voice"):
        emit("media_too_big", {"kind": "voice"}); return
    payload = {"salon": me["salon"], "pseudo": me["pseudo"],
               "avatar": me.get("avatar", ""), "audio": data,
               "dur": float(d.get("dur", 0) or 0), "ts": time.time()}
    _recent_push(me["salon"], payload)
    socketio.emit("voice", payload, to=me["salon"], include_self=False)


@socketio.on("video")
def on_video(d):
    """Message video court : diffuse en direct au salon (ephemere, jamais en base)."""
    me = _me()
    if not me or not me["salon"]:
        return
    data = d.get("data", "")
    if not data:
        return
    if not _flood_ok(request.sid, "media") or not _can_write(me, me["salon"]):
        return
    if _too_big(data, "video"):
        emit("media_too_big", {"kind": "video"}); return
    payload = {"salon": me["salon"], "pseudo": me["pseudo"],
               "avatar": me.get("avatar", ""), "video": data,
               "name": (d.get("name") or "")[:80], "ts": time.time()}
    _recent_push(me["salon"], payload)
    socketio.emit("video", payload, to=me["salon"], include_self=False)


@socketio.on("salon_file")
def on_salon_file(d):
    """Fichier quelconque partage dans le salon (ephemere, jamais en base)."""
    me = _me()
    if not me or not me["salon"]:
        return
    data = d.get("data", "")
    if not data:
        return
    if not _flood_ok(request.sid, "media") or not _can_write(me, me["salon"]):
        return
    if _too_big(data, "file"):
        emit("media_too_big", {"kind": "file"}); return
    payload = {"salon": me["salon"], "pseudo": me["pseudo"],
               "avatar": me.get("avatar", ""), "data": data,
               "name": (d.get("name") or "fichier")[:120],
               "mime": (d.get("mime") or "")[:80],
               "size": int(d.get("size", 0) or 0), "ts": time.time()}
    _recent_push(me["salon"], payload)
    socketio.emit("salon_file", payload, to=me["salon"], include_self=False)


@socketio.on("react")
def on_react(d):
    """Reaction emoji sur un message : diffusee a tout le salon."""
    me = _me()
    if not me or not me["salon"]:
        return
    mid = (d.get("mid") or "")[:120]
    emo = (d.get("emo") or "")[:8]
    if not mid or not emo:
        return
    socketio.emit("react", {"salon": me["salon"], "mid": mid, "emo": emo,
                  "pseudo": me["pseudo"]}, to=me["salon"])


@socketio.on("set_wallpaper")
def on_set_wallpaper(d):
    """Un admin/owner definit le fond d'ecran du salon courant pour TOUS.
    L'image reste ephemere : diffusee en direct, jamais stockee en base."""
    me = _me()
    if not me or not me["salon"]:
        return
    if me["role"] not in ("admin", "owner"):
        emit("denied", {"raison": "Reserve aux admins."}); return
    data = d.get("data", "")
    if data and len(data) > 900 * 1024:
        emit("denied", {"raison": "Fond trop lourd (900 Ko max)."}); return
    socketio.emit("set_wallpaper", {"salon": me["salon"], "data": data,
                  "by": me["pseudo"]}, to=me["salon"])
    socketio.emit("system", {"salon": me["salon"], "bot": "Network",
                  "text": "\U0001F5BC\uFE0F %s a change le fond d'ecran du salon." % me["pseudo"]},
                  to=me["salon"])


@socketio.on("change_pseudo")
def on_change_pseudo(d):
    """Changer son pseudo : verifie l'unicite puis diffuse le nouveau nom."""
    me = _me()
    if not me:
        return
    new = (d.get("pseudo") or "").strip()[:24]
    if not new or len(new) < 2:
        emit("pseudo_error", {"error": "Pseudo trop court (2+)"}); return
    ex = sql.get_by_pseudo(new)
    if ex and ex["browser_id"] != me["bid"]:
        emit("pseudo_error", {"error": "Pseudo deja pris"}); return
    old = me["pseudo"]
    if new == old:
        emit("pseudo_ok", {"pseudo": new}); return
    sql.update_user(me["bid"], {"pseudo": new})
    for info in SID.values():
        if info["bid"] == me["bid"]:
            info["pseudo"] = new
    emit("pseudo_ok", {"pseudo": new, "user": sql.get_user(me["bid"])})
    if me["salon"]:
        _push_online(me["salon"])
        socketio.emit("system", {"salon": me["salon"], "bot": "Network",
                      "text": "\u270f\ufe0f %s s'appelle maintenant %s." % (old, new)}, to=me["salon"])


@socketio.on("salon_media")
def on_salon_media(d):
    """Annonce d'une video partagee dans le salon courant (flux 100%% P2P).
    On ne diffuse QUE les metadonnees ; le fichier transite ensuite en WebRTC."""
    me = _me()
    if not me or not me["salon"]:
        return
    socketio.emit("salon_media", {"salon": me["salon"], "host": me["bid"],
                  "hostPseudo": me["pseudo"], "avatar": me.get("avatar", ""),
                  "name": d.get("name"), "mime": d.get("mime", ""),
                  "ts": time.time()}, to=me["salon"], include_self=False)


@socketio.on("salon_media_req")
def on_salon_media_req(d):
    """Un membre demande a recevoir la video : on relaie la demande vers l'hote."""
    me = _me()
    if not me:
        return
    for s, info in SID.items():
        if info["bid"] == d.get("host"):
            socketio.emit("salon_media_req", {"from": me["bid"], "pseudo": me["pseudo"]}, to=s)
            break


@socketio.on("pm")
def on_pm(d):
    me = _me()
    if not me:
        return
    cible = None
    code = (d.get("code") or "").strip()
    if code:
        cible = _find_by_code(code)
    if not cible:
        cible = sql.get_by_pseudo(d.get("to", ""))
    if not cible:
        emit("pm_error", {"error": "Introuvable"}); return
    if cible["browser_id"] == me["bid"]:
        emit("pm_error", {"error": "Tu ne peux pas t'ecrire a toi-meme."}); return
    # blocage : la cible t'a bloque OU tu l'as bloquee -> rien ne passe
    me_user = sql.get_user(me["bid"]) or {}
    if me["bid"] in (cible.get("blocked") or []):
        emit("pm_error", {"error": "Cette personne t'a bloque."}); return
    if cible["browser_id"] in (me_user.get("blocked") or []):
        emit("pm_error", {"error": "Tu as bloque cette personne. Debloque-la pour ecrire."}); return
    txt = (d.get("text") or "")[:2000]
    if not txt.strip():
        return
    payload = {"from": me["pseudo"], "to": cible["pseudo"], "text": txt, "ts": time.time()}
    targets = [s for s, info in SID.items() if info["bid"] == cible["browser_id"]]
    # echo chez l'expediteur (toujours)
    for s, info in list(SID.items()):
        if info["bid"] == me["bid"]:
            socketio.emit("pm", payload, to=s)
    if targets:
        for s in targets:
            socketio.emit("pm", payload, to=s)
    else:
        # HORS LIGNE : le message reste en file et part des son retour
        try:
            sql.add_pending_pm(cible["browser_id"], payload)
        except Exception:
            pass
        emit("pm_queued", {"to": cible["pseudo"], "ts": payload["ts"]})


@socketio.on("pm_resolve")
def on_pm_resolve(d):
    """Ouvre une discussion a partir d'un NUMERO PERSO distribue par la personne."""
    me = _me()
    if not me:
        return
    u = _find_by_code(d.get("code", ""))
    if not u:
        emit("pm_error", {"error": "Aucun membre avec ce numero."}); return
    if u["browser_id"] == me["bid"]:
        emit("pm_error", {"error": "C'est ton propre numero \U0001F642"}); return
    online = any(info["bid"] == u["browser_id"] for info in SID.values())
    emit("pm_opened", {"pseudo": u["pseudo"], "online": online,
                       "code": _fmt_code(_user_code(u["browser_id"]))})


@socketio.on("pm_block")
def on_pm_block(d):
    """Bloque/debloque une personne (par pseudo). Persiste sur le compte."""
    me = _me()
    if not me:
        return
    cible = sql.get_by_pseudo(d.get("pseudo", ""))
    if not cible:
        emit("pm_error", {"error": "Introuvable"}); return
    u = sql.get_user(me["bid"]) or {}
    blocked = list(u.get("blocked") or [])
    bid = cible["browser_id"]
    if d.get("unblock"):
        blocked = [b for b in blocked if b != bid]
        state = False
    else:
        if bid not in blocked:
            blocked.append(bid)
        state = True
    try:
        sql.update_user(me["bid"], {"blocked": blocked})
    except Exception:
        pass
    emit("pm_block_ok", {"pseudo": cible["pseudo"], "blocked": state})


@socketio.on("buy_premium")
def on_buy_premium(d):
    """Active le mode Premium (badge dore, effets waouh). Cout: 500 points."""
    me = _me()
    if not me:
        return
    u = sql.get_user(me["bid"]) or {}
    if u.get("premium"):
        emit("premium_ok", {"user": u}); return
    cost = 500
    if int(u.get("points", 0)) < cost:
        emit("pm_error", {"error": "Il te faut 500 points pour Premium."}); return
    try:
        sql.update_user(me["bid"], {"points": int(u.get("points", 0)) - cost, "premium": True})
    except Exception:
        pass
    nu = sql.get_user(me["bid"]) or u
    emit("premium_ok", {"user": nu})


# --- Boutique de petits cadeaux virtuels a offrir aux membres ---
GIFTS = {
    "fleur":    {"emoji": "\U0001F338", "nom": "Fleur",    "prix": 5},
    "cafe":     {"emoji": "\u2615",      "nom": "Cafe",     "prix": 8},
    "ballon":   {"emoji": "\U0001F388", "nom": "Ballon",   "prix": 10},
    "donut":    {"emoji": "\U0001F369", "nom": "Donut",    "prix": 15},
    "coeur":    {"emoji": "\u2764\ufe0f", "nom": "Coeur",  "prix": 20},
    "cadeau":   {"emoji": "\U0001F381", "nom": "Cadeau",   "prix": 30},
    "diamant":  {"emoji": "\U0001F48E", "nom": "Diamant",  "prix": 60},
    "couronne": {"emoji": "\U0001F451", "nom": "Couronne", "prix": 90},
    "fusee":    {"emoji": "\U0001F680", "nom": "Fusee",    "prix": 120},
}


@socketio.on("offer_gift")
def on_offer_gift(d):
    """Offrir un petit cadeau virtuel a un membre (deduit des points, visible par le salon)."""
    me = _me()
    if not me:
        return
    g = GIFTS.get(str(d.get("gift", "")))
    if not g:
        emit("pm_error", {"error": "Cadeau inconnu."}); return
    cible = sql.get_by_pseudo(d.get("to", ""))
    if not cible:
        emit("pm_error", {"error": "Destinataire introuvable."}); return
    u = sql.get_user(me["bid"]) or {}
    solde = int(u.get("points", 0))
    if solde < g["prix"]:
        emit("pm_error", {"error": "Pas assez de \U0001FA99 pour ce cadeau (%d)." % g["prix"]}); return
    try:
        sql.update_user(me["bid"], {"points": solde - g["prix"]})
    except Exception:
        pass
    emit("economy", {"points": solde - g["prix"]})
    payload = {"from": me["pseudo"], "to": cible["pseudo"], "emoji": g["emoji"],
               "nom": g["nom"], "ts": time.time()}
    if me.get("salon"):
        socketio.emit("gift", dict(payload, salon=me["salon"]), to=me["salon"])
    for s, info in SID.items():
        if info.get("bid") == cible.get("browser_id"):
            socketio.emit("gift_dm", payload, to=s)



@socketio.on("pm_voice")
def on_pm_voice(d):
    """Note vocale en message prive, relayee par le serveur (ephemere, pas de P2P, pas de base)."""
    me = _me()
    if not me:
        return
    cible = sql.get_by_pseudo(d.get("to", ""))
    if not cible:
        emit("pm_error", {"error": "Introuvable"}); return
    data = d.get("data", "")
    if not data:
        emit("pm_error", {"error": "Vocal vide"}); return
    payload = {"from": me["pseudo"], "to": cible["pseudo"], "audio": data,
               "dur": float(d.get("dur", 0) or 0), "ts": time.time()}
    for s, info in list(SID.items()):
        if info["bid"] in (cible["browser_id"], me["bid"]):
            socketio.emit("pm_voice", payload, to=s)


@socketio.on("pm_media")
def on_pm_media(d):
    """Photo / video / fichier en message prive, relaye par le serveur (ephemere).
    Non stocke : si le destinataire est hors ligne, on previent l'expediteur."""
    me = _me()
    if not me:
        return
    cible = _find_by_code(d.get("code", "")) or sql.get_by_pseudo(d.get("to", ""))
    if not cible:
        emit("pm_error", {"error": "Introuvable"}); return
    data = d.get("data", "")
    if not data:
        return
    me_user = sql.get_user(me["bid"]) or {}
    if me["bid"] in (cible.get("blocked") or []):
        emit("pm_error", {"error": "Cette personne t'a bloque."}); return
    if cible["browser_id"] in (me_user.get("blocked") or []):
        emit("pm_error", {"error": "Tu as bloque cette personne."}); return
    payload = {"from": me["pseudo"], "to": cible["pseudo"], "kind": (d.get("kind") or "file")[:10],
               "data": data, "name": (d.get("name") or "fichier")[:120],
               "mime": (d.get("mime") or "")[:80], "size": int(d.get("size", 0) or 0),
               "ts": time.time()}
    targets = [s for s, info in SID.items() if info["bid"] == cible["browser_id"]]
    # echo chez l'expediteur
    for s, info in list(SID.items()):
        if info["bid"] == me["bid"]:
            socketio.emit("pm_media", payload, to=s)
    if targets:
        for s in targets:
            socketio.emit("pm_media", payload, to=s)
    else:
        emit("pm_media_offline", {"to": cible["pseudo"]})


@socketio.on("user_info")
def on_user_info(d):
    u = sql.get_by_pseudo(d.get("pseudo", ""))
    if not u:
        return
    av = ""
    for info in SID.values():
        if info["pseudo"] == u["pseudo"]:
            av = info.get("avatar", "")
    emit("user_info", {"pseudo": u["pseudo"], "level": u.get("level", 1),
         "titre": u.get("titre_actif"), "role": u.get("role", "user"),
         "pays": u.get("pays", ""), "avatar": av})


@socketio.on("buy_title")
def on_buy(d):
    me = _me()
    if not me:
        return
    ok, res = squad.buy_title(me["bid"], int(d.get("titre_id", 0)))
    if ok:
        emit("title_bought", {"titre": res, "user": sql.get_user(me["bid"])})
    else:
        emit("shop_error", {"error": res})


@socketio.on("become_mod")
def on_become_mod(d):
    me = _me()
    if not me:
        return
    user = sql.get_user(me["bid"])
    squad.demande_mod(user, (d.get("raison") or "")[:400], request.headers.get("Origin", ""))
    emit("mod_ok", {"text": "Demande envoyee au proprietaire (%s)." % config.SUPPORT_EMAIL})


@socketio.on("post_annonce")
def on_post_annonce(d):
    me = _me()
    if not me:
        return
    user = sql.get_user(me["bid"])
    ok, res = squad.post_annonce(user, (d.get("texte") or "").strip(), d.get("heures", 1))
    if ok:
        emit("economy", {"points": sql.get_user(me["bid"]).get("points", 0)})
    else:
        emit("shop_error", {"error": res})


@socketio.on("post_secret")
def on_post_secret(d):
    me = _me()
    if not me:
        return
    user = sql.get_user(me["bid"])
    ok, res = squad.post_secret(user, (d.get("titre") or "").strip(),
                                (d.get("desc") or "").strip(), (d.get("contenu") or "").strip(),
                                d.get("prix", 0), d.get("heures", 1))
    emit("secret_posted", {"ok": ok, "secret": res if ok else None})


@socketio.on("buy_secret")
def on_buy_secret(d):
    me = _me()
    if not me:
        return
    sid = int(d.get("secret_id", 0))
    ok, res = squad.buy_secret(me["bid"], sid)
    if ok:
        emit("secret_reveal", {"secret_id": sid, "contenu": res})
        emit("economy", {"points": sql.get_user(me["bid"]).get("points", 0)})
    else:
        emit("shop_error", {"error": res})


@socketio.on("approve_mod")
def on_approve(d):
    me = _me()
    if not me or me["role"] != "owner":
        return
    squad.approve(int(d.get("req_id", 0)), d.get("browser_id"))
    emit("panel_refresh", {})


@socketio.on("refuse_mod")
def on_refuse(d):
    me = _me()
    if not me or me["role"] != "owner":
        return
    squad.refuse(int(d.get("req_id", 0)))
    emit("panel_refresh", {})


@socketio.on("kick_mod")
def on_kick(d):
    me = _me()
    if not me or me["role"] != "owner":
        return
    squad.kick_mod(d.get("browser_id"))
    emit("panel_refresh", {})


@socketio.on("reserve_place")
def on_reserve(d):
    me = _me()
    if not me:
        return
    user = sql.get_user(me["bid"])
    ok, res = squad.reserve_place(user, d.get("salon", ""))
    if ok:
        me["resa"] = d.get("salon")
        emit("reserve_ok", {"salon": d.get("salon"), "state": res})
        emit("economy", {"points": sql.get_user(me["bid"]).get("points", 0)})
    else:
        emit("shop_error", {"error": res})


@socketio.on("cancel_place")
def on_cancel(d):
    me = _me()
    if not me:
        return
    user = sql.get_user(me["bid"])
    ok, res = squad.cancel_place(user, d.get("salon", ""))
    if me.get("salon") == d.get("salon"):
        leave_room(me["salon"]); s = me["salon"]; me["salon"] = None; _push_online(s)
    emit("reserve_ok", {"salon": d.get("salon"), "state": res, "cancelled": True})


def _cine_ok(me):
    return (not config.CINEMA_SECURE) or me.get("cine_ticket")


@socketio.on("film_load")
def on_film_load(d):
    me = _me()
    if not me or me["salon"] != "cinema":
        return
    if d.get("mode") == "perso":
        return
    if not _cine_ok(me):
        emit("denied", {"raison": "Billet requis pour diffuser (tape /ticket)."}); return
    socketio.emit("film_load", {"name": d.get("name"), "host": me["bid"],
                  "hostPseudo": me["pseudo"], "mime": d.get("mime", "")}, to="cinema")


@socketio.on("film_play")
def on_film_play(d):
    me = _me()
    if not me or me["salon"] != "cinema":
        return
    socketio.emit("film_play", {"time": d.get("time", 0), "playing": d.get("playing", True)}, to="cinema")


@socketio.on("film_join")
def on_film_join(d):
    me = _me()
    if not me:
        return
    if not _cine_ok(me):
        emit("denied", {"raison": "Billet requis pour voir le film (tape /ticket)."}); return
    for s, info in SID.items():
        if info["bid"] == d.get("host"):
            socketio.emit("film_request", {"from": me["bid"], "pseudo": me["pseudo"]}, to=s)


@socketio.on("rtc_signal")
def on_rtc(d):
    me = _me()
    if not me:
        return
    if me.get("salon") == "cinema" and not _cine_ok(me):
        return
    d["from"] = me["bid"]
    for s, info in SID.items():
        if info["bid"] == d.get("to"):
            socketio.emit("rtc_signal", d, to=s)
            break


@socketio.on("p2p_offer_file")
def on_p2p_offer(d):
    """Annonce d'un gros fichier : on ne diffuse QUE les metadonnees.
    Le fichier lui-meme transite ensuite 100%% en P2P (WebRTC DataChannel),
    il ne passe JAMAIS par le serveur."""
    me = _me()
    if not me or not me["salon"]:
        return
    if not _flood_ok(request.sid, "media") or not _can_write(me, me["salon"]):
        return
    socketio.emit("p2p_offer_file", {"fid": str(d.get("fid", ""))[:64],
                  "name": (d.get("name") or "fichier")[:120],
                  "mime": (d.get("mime") or "")[:80],
                  "size": int(d.get("size", 0) or 0),
                  "host": me["pseudo"], "avatar": me.get("avatar", ""),
                  "ts": time.time()}, to=me["salon"], include_self=False)


@socketio.on("p2p_want")
def on_p2p_want(d):
    """Un pair veut recevoir le fichier : on previent l'hote."""
    me = _me()
    if not me or not me["salon"]:
        return
    for s in _sids_by_pseudo(d.get("host", ""), me["salon"]):
        socketio.emit("p2p_want", {"fid": str(d.get("fid", ""))[:64],
                      "from": me["pseudo"]}, to=s)


@socketio.on("p2p_sig")
def on_p2p_sig(d):
    """Relais de signaling P2P (SDP / ICE) entre deux pairs du meme salon."""
    me = _me()
    if not me or not me["salon"]:
        return
    for s in _sids_by_pseudo(d.get("to", ""), me["salon"]):
        socketio.emit("p2p_sig", {"fid": str(d.get("fid", ""))[:64],
                      "from": me["pseudo"], "sdp": d.get("sdp"),
                      "ice": d.get("ice")}, to=s)


@socketio.on("disconnect")
def on_disc():
    me = SID.pop(request.sid, None)
    FLOOD.pop(request.sid, None)
    if me:
        try:
            sql.update_user(me["bid"], {"last_seen": time.time()})
        except Exception:
            pass
        if me["salon"]:
            _push_online(me["salon"])
            if _bid_in_salon(me["bid"], me["salon"]) == 0:
                _presence(me["salon"], me["pseudo"], "leave", bid=me["bid"])
                # passe l'ex-membre en "rouge" cote client (disparait apres 5 min)
                socketio.emit("member_off", {"pseudo": me["pseudo"],
                              "avatar": me.get("avatar", ""),
                              "role": me.get("role", "user")}, to=me["salon"])


if __name__ == "__main__":
    print("HIKAROCHAT en ligne sur %s:%d" % (config.HOST, config.PORT))
    socketio.run(app, host=config.HOST, port=config.PORT, allow_unsafe_werkzeug=True)
