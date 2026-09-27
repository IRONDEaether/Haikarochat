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
                    ping_timeout=25, ping_interval=15,
                    message_queue=(config.MESSAGE_QUEUE or None))

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


def _sig(bid):
    return hashlib.sha256((bid + config.SECRET_KEY).encode()).hexdigest()


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
    """Liste des membres d'un salon : bots (toujours) + humains connectes."""
    membres = [{"pseudo": b["nom"], "role": "bot", "icone": b["icone"],
                "desc": b["desc"], "avatar": ""} for b in config.BOTS]
    for info in SID.values():
        if info.get("salon") == salon:
            membres.append({"pseudo": info["pseudo"], "role": info["role"],
                            "avatar": info.get("avatar", ""), "desc": info.get("desc", "")})
    return membres


def _push_online(salon):
    m = _online(salon)
    socketio.emit("online", {"salon": salon, "membres": m, "count": len(m)}, to=salon)


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
    return render_template("loading.html", support=config.SUPPORT_EMAIL)


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
    SID[request.sid] = {"bid": bid, "pseudo": user["pseudo"], "role": user.get("role", "user"),
                        "salon": None, "avatar": d.get("avatar", "")}
    emit("auth_ok", {"user": user, "salons": _salons_for(user),
                     "titres": config.TITRES, "bots": config.BOTS,
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
    join_room(salon)
    me["salon"] = salon
    emit("history", {"salon": salon, "messages": sql.history(salon)})
    squad.bienvenue(salon, user)
    _push_online(salon)
    if me.get("avatar"):
        socketio.emit("avatar", {"pseudo": me["pseudo"], "avatar": me["avatar"]}, to=salon)


@socketio.on("leave")
def on_leave(d=None):
    me = _me()
    if not me or not me["salon"]:
        return
    s = me["salon"]; leave_room(s); me["salon"] = None; _push_online(s)


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
    ok, _ = squad.inspect(me["bid"], me["pseudo"], txt, salon)
    if not ok:
        return
    user = sql.get_user(me["bid"])
    pref = "@" if me["role"] in ("modo", "admin", "owner") else ""
    payload = {"salon": salon, "pseudo": pref + me["pseudo"], "titre": user.get("titre_actif"),
               "avatar": me.get("avatar", ""), "text": txt, "ts": time.time()}
    sql.add_message(salon, me["bid"], me["pseudo"], txt)
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
        cmds = "/help /me /roll /rules /online"
        if is_mod:
            cmds += " | MODO: /ban <pseudo> <raison> /warn <pseudo> <raison>"
        cmds += " | CASINO: /de /pileface <mise> /machine /defi <txt> | CINE: /ticket"
        emit("system", {"salon": salon, "bot": "Haiku", "text": "Commandes: " + cmds})
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
    if len(data) > 300 * 1024:
        emit("denied", {"raison": "Trop lourd -> P2P"}); return
    socketio.emit("image", {"salon": me["salon"], "pseudo": me["pseudo"],
                  "avatar": me.get("avatar", ""), "image": data, "ts": time.time()}, to=me["salon"])


@socketio.on("pm")
def on_pm(d):
    me = _me()
    if not me:
        return
    cible = sql.get_by_pseudo(d.get("to", ""))
    if not cible:
        emit("pm_error", {"error": "Introuvable"}); return
    payload = {"from": me["pseudo"], "to": cible["pseudo"], "text": (d.get("text") or "")[:2000], "ts": time.time()}
    for s, info in list(SID.items()):
        if info["bid"] in (cible["browser_id"], me["bid"]):
            socketio.emit("pm", payload, to=s)


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


@socketio.on("disconnect")
def on_disc():
    me = SID.pop(request.sid, None)
    if me and me["salon"]:
        _push_online(me["salon"])


if __name__ == "__main__":
    print("HIKAROCHAT en ligne sur %s:%d" % (config.HOST, config.PORT))
    socketio.run(app, host=config.HOST, port=config.PORT, allow_unsafe_werkzeug=True)
