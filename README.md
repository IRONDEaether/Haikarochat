# HIKAROCHAT (v1.1.1)

Chat communautaire en temps réel (Python + Flask-SocketIO) avec une **escouade de bots**
toujours présente, des salons thématiques, une boutique de titres, un salon
Divertissement (film synchronisé en P2P + musique), une **Salle Privée sur réservation**
et un espace propriétaire.

## Nouveautés v1.1.1
- **Base MySQL durable** (recommandée) : voir la section « Base de données » ci-dessous.
- **Chat corrigé** : auto-scroll fiable (reste en bas quand tu écris), la barre de saisie
  ne disparaît plus, et les messages ne se chevauchent plus avec le nom/la photo.
- **Interface 100% responsive** (téléphone + tablette + PC), modulaire.
- **Tickets/réservations temporaires** (en mémoire, avec expiration) — jamais écrits en base.

## Points clés
- **Pages HTML autonomes** : chaque template embarque son CSS + JS (aucun fichier statique séparé).
- **Bots posés à côté du cerveau** (`bots/`) et affichés **en permanence** comme membres connectés.
- **Aucune photo en base** : les avatars restent chez le client (localStorage) et sont
  **diffusés par socket** aux membres du salon à la connexion. Les images de chat lourdes
  et les films passent en **P2P (WebRTC)**.
- **Clic sur un membre** = fiche d'infos publiques ; **double-clic** = message privé.
- **Modération** via le Bot Network : `/ban <pseudo> <raison>` et `/warn <pseudo> <raison>`
  (réservées aux modo/admin/owner). Le `@` préfixe automatiquement les messages du staff.
- **Propriétaire** : page `/owner` sécurisée par pseudo + mot de passe + `OWNER_CODE`.

## Installation
```bash
pip install -r requirements.txt
cp .env.example .env    # puis édite tes clés / codes
python cerveau.py
```
Ouvre ensuite `http://localhost:5000`.

## Base de données — OÙ METTRE L'URL MySQL
HIKAROCHAT choisit son backend **automatiquement**, dans cet ordre :
1. **MySQL** si `DATABASE_URL` est défini — **recommandé en production**.
2. **Supabase** si `SUPABASE_URL` + `SUPABASE_KEY`.
3. **JSON local** (`data/local.json`) sinon — dev uniquement, **non persistant**.

### Colle ton URL MySQL ici
- **En local** : dans le fichier `.env`, ligne :
  ```
  DATABASE_URL=mysql://UTILISATEUR:MOTDEPASSE@HOTE:3306/NOM_BASE
  ```
- **Sur Render** : Dashboard du service → onglet **Environment** → **Add Variable**
  - Key = `DATABASE_URL`
  - Value = `mysql://UTILISATEUR:MOTDEPASSE@HOTE:3306/NOM_BASE`

Formats acceptés : `mysql://...` ou `mysql+pymysql://...`.
La table `hikaro_store` est **créée automatiquement** au premier démarrage
(aucun script SQL à lancer). Elle stocke, en texte uniquement (jamais d'images) :
comptes, points/XP, titres, messages récents, bans, annonces et secrets.

> ⚠️ **Pourquoi MySQL sur Render ?** Le disque de Render est **éphémère** : en JSON local,
> tous les comptes/points sont perdus à chaque redéploiement. MySQL les rend **durables**.
>
> Les **tickets de cinéma et les réservations de la Salle Privée** restent **temporaires**
> (en mémoire, avec expiration) : ils ne sont volontairement pas écrits en base.

Si tu préfères Supabase, applique d'abord `db/schema.sql`.

## Salons
Général, Otaku, Manga, Tech, École, **Divertissement** (Ensemble/Perso), Premium (par niveau),
Aide (bot d'assistance), Admin (staff).

### 🕶️ Salon secret (Club Secret)
Le salon secret est **caché** de la liste : son nom, sa description et son prix sont masqués.
Pour le débloquer, un membre tape simplement le mot **`secret`** dans le chat. S'il a assez
de points (500 par défaut, réglable via `prix` dans `config.py`), le montant est déduit et le
salon **apparaît dans sa liste** — les autres ne le voient pas.

## Deux serveurs (haute dispo)
Pour que **deux serveurs gèrent la charge** et qu'une panne de l'un ne fasse pas cracher l'autre :
1. Lance un **Redis** partagé.
2. Mets la même `MESSAGE_QUEUE=redis://.../0` dans le `.env` des deux instances, avec un
   `INSTANCE_ID` différent (`hikaro-1`, `hikaro-2`) et un `PORT` différent.
3. Place un **reverse-proxy** (nginx) devant, avec **sticky sessions** (ip_hash) et la sonde
   `/health` pour retirer une instance morte automatiquement.

```
INSTANCE_ID=hikaro-1 PORT=5000 MESSAGE_QUEUE=redis://localhost:6379/0 python cerveau.py
INSTANCE_ID=hikaro-2 PORT=5001 MESSAGE_QUEUE=redis://localhost:6379/0 python cerveau.py
```

> ⚠️ Honnêteté : la file Redis synchronise bien les **messages, bans et événements** entre
> instances. En revanche, la **liste des connectés** est pour l'instant locale à chaque
> instance (présence non partagée). Pour une présence 100% globale, il faudra stocker les
> sessions dans Redis — étape suivante recommandée.

## Divertissement
- **Ensemble** : le film reste chez l'hôte ; il est envoyé en P2P à ceux qui rejoignent,
  et la lecture est **synchronisée** (play/pause/seek).
- **Perso** : lecture 100% locale.
- **Zone musique** : lecture audio locale pour l'ambiance.

## Sécurité
- Empreinte navigateur (browser_id) + signature serveur pour l'auth socket.
- Bannissements et avertissements journalisés.
- À mettre derrière **HTTPS** en production (WebRTC + caméra/fichiers l'exigent hors localhost).

Support : raphaelod760@protonmail.com
