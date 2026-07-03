# Hosting trellno on a Linux box

This turns the rented Linux server into the **canonical home** of the boards:
`trello serve` runs there over a local file store, Caddy gives it HTTPS, and
every client — your machines, other agents, Claude cloud sessions — talks to it
with `trello --backend http` (or the browser UI). The store lock lives on one
machine, so `grab` and every other write are truly atomic for *all* clients;
the old Dropbox last-write-wins problem disappears.

Files here:

- `trellno.service` — systemd unit (loopback bind, token-gated, hardened)
- `trellno.env.example` — its environment file (`/etc/trellno.env`)
- `Caddyfile` — HTTPS + reverse proxy site block

Placeholders used below: domain `trellno.example.com`, install root
`/srv/trellno`. Substitute your own.

## 1. Provision

Needs Python ≥ 3.10 and Caddy (or any TLS-terminating proxy).

```bash
sudo useradd --system --home /srv/trellno --shell /usr/sbin/nologin trellno
sudo mkdir -p /srv/trellno/data
sudo python3 -m venv /srv/trellno/venv
sudo /srv/trellno/venv/bin/pip install "trello-cli[web] @ git+https://github.com/Coamithra/TrelloCLI.git"
sudo chown -R trellno:trellno /srv/trellno
```

(For a private repo, install from a checkout you `git clone`d with a deploy
key/token instead of the direct `git+https` URL.)

## 2. Move the store (one-time cutover)

Copy the existing Dropbox store to the server — the folder that holds
`<boardId>/board.json` etc. (the configured `local_root`). From the machine
that has it:

```bash
rsync -av --exclude '.lock' "/path/to/FakeTrelloData/" user@server:/tmp/trellno-data/
# then on the server:
sudo rsync -a /tmp/trellno-data/ /srv/trellno/data/ && sudo chown -R trellno:trellno /srv/trellno/data
```

From this point the **server copy is canonical**. Stop editing the Dropbox
copy (keep it as a cold backup if you like); local edits there no longer reach
the server. To keep working offline-ish, always go through `--backend http`.

## 3. Configure + start the service

```bash
sudo cp deploy/trellno.service /etc/systemd/system/trellno.service
sudo cp deploy/trellno.env.example /etc/trellno.env
sudo chmod 600 /etc/trellno.env
sudoedit /etc/trellno.env        # set TRELLNO_TOKEN (openssl rand -base64 32) + TRELLNO_DOMAIN
sudo systemctl daemon-reload
sudo systemctl enable --now trellno
systemctl status trellno         # should be active; listens on 127.0.0.1:8787
```

## 4. HTTPS with Caddy

```bash
sudo apt install caddy           # Debian/Ubuntu
# append deploy/Caddyfile (with your domain) to /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

DNS: point `trellno.example.com` at the server first; Caddy then provisions
the certificate automatically. Never expose port 8787 directly — only Caddy
should reach it (the loopback bind enforces this).

Smoke test:

```bash
curl -s https://trellno.example.com/api/boards            # → 401 (token gate works)
curl -s "https://trellno.example.com/api/boards?token=<TRELLNO_TOKEN>"  # → board list
```

The browser UI is `https://trellno.example.com/?token=<TRELLNO_TOKEN>`.

## 5. Point the CLI at it (every machine)

```bash
trello configure-http https://trellno.example.com <TRELLNO_TOKEN>
trello --backend http boards
trello --backend http --board 6a353ffc list ls
```

Or per-session with no persisted config (what CI / cloud sessions use):
`TRELLO_BACKEND=http`, `TRELLO_SERVER=https://trellno.example.com`,
`TRELLO_SERVER_TOKEN=<token>`.

Everything works over http — including the atomic
`trello --backend http --board 6a353ffc grab --from "To Do" --to "Doing"`
(the claim executes under the server's store lock, so concurrent grabbers on
different machines still get distinct cards).

## 6. Claude cloud sessions

1. In the cloud environment's **network policy**, allow `trellno.example.com`.
2. Set the three env vars above in the environment configuration (or a
   SessionStart setup script), with the token stored as a secret.
3. Sessions can then run the normal board runbook (`grab`, `card`, `comment`,
   …) via the installed CLI, or read the JSON API directly.

## 7. Backups

The store is plain files — snapshot it with cron, e.g. daily:

```
0 3 * * * tar -C /srv/trellno -czf /srv/trellno/backup/data-$(date +\%F).tar.gz data && ls -1t /srv/trellno/backup | tail -n +15 | xargs -r -I{} rm /srv/trellno/backup/{}
```

(Or rsync `/srv/trellno/data` back into a Dropbox-synced folder on your
machine as the offsite copy.)

## Upgrades

```bash
sudo /srv/trellno/venv/bin/pip install --upgrade "trello-cli[web] @ git+https://github.com/Coamithra/TrelloCLI.git"
sudo systemctl restart trellno
```
