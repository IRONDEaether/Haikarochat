-- HIKAROCHAT - schema Supabase (texte uniquement, AUCUNE image).

create table if not exists users (
  browser_id     text primary key,
  ip_address     text,
  signature_hash text,
  pseudo         text not null,
  age            int,
  sexe           text,
  pays           text,
  pass_hash      text,          -- owner/admin (optionnel)
  xp             int default 0,
  level          int default 1,
  points         int default 0,
  titre_actif    text,
  secret_unlocked boolean default false,   -- acces au Club Secret
  role           text default 'user',   -- user | modo | admin | owner
  created_at     timestamptz default now()
);

create table if not exists messages (
  id         bigserial primary key,
  salon      text,
  browser_id text,
  pseudo     text,
  contenu    text,
  type       text default 'text',
  ts         double precision
);

create table if not exists bans (
  id         bigserial primary key,
  browser_id text,
  pseudo     text,
  raison     text,
  auteur     text,
  created_at timestamptz default now()
);

-- NB : les photos/medias ne sont JAMAIS stockes ici (P2P + localStorage).
