# Otomo Production Deployment

This deployment shape runs one API worker plus one independent weekly worker.
The API process keeps request handling predictable; the weekly worker owns
scheduled digest generation so API restarts do not duplicate or interrupt jobs.

## First deploy checklist

1. Point `OTOMO_DOMAIN` to the server. Mainland China public web hosting needs
   ICP filing; Hong Kong or overseas nodes avoid that requirement.
2. Copy `deploy/production.env.example` into `backend/.env` and fill real
   secrets. Generate a fixed Fernet key for `AUTH_ENCRYPTION_KEY`; do not use a
   container-generated development key in production.
3. Set Bangumi OAuth redirect URI to
   `https://your-domain.example/auth/bangumi/callback`.
4. Keep the security group/firewall to `80/tcp` and `443/tcp` only.
5. Run `docker compose up -d --build`.
6. Test OAuth login, image upload, chat, weekly inbox generation, and one
   webhook channel.
7. Configure a daily cache backup and run a restore drill in the first week.

## Runtime notes

- `backend` sets `WEEKLY_SCHEDULER_ENABLED=false`.
- `weekly` runs `python -m otomo.weekly_daemon`.
- `./cache` contains auth/session/LTM/uploads and must be backed up.
- `./models` contains large local model files and should not be baked into
  images.
- `./logs` should be volume-mounted if you enable production access logs.

## Restore drill

1. Stop services.
2. Move the current `cache` directory aside.
3. Restore the backup archive into `cache`.
4. Start services and verify OAuth identity, session history, memory, inbox,
   and weekly subscription state.
