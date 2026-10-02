# Rotating Leaked Secrets (Windows PowerShell)

Your real `.env` file was uploaded to the public GitHub repo. Anyone could have
copied it. Deleting the file is **not enough**: the old values are still in git
history. The fix is to **replace every secret with a new one**, so the leaked
copies stop working.

Do the steps in order. Never paste a secret value into chat, email, or a
screenshot.

---

## Step 0. Open PowerShell in your project folder

```powershell
cd "C:\path\to\Food ordering system"
```

- `cd` = change directory. Replace the path with where your project lives.
- Check you're in the right place: `dir .env` should list the file.

---

## Step 1. Make the repo private (2 minutes, do this first)

1. Go to https://github.com/ephreme96/Food-ordering-system
2. **Settings** → scroll to the bottom → **Danger Zone** → **Change visibility** → **Make private**.

Why: stops new people from seeing the history while you rotate.
It does **not** undo what was already exposed, so keep going.

---

## Step 2. Stop tracking `.env` in git

This is already done in the pull request that added this file. If you ever
need to do it yourself:

```powershell
git rm --cached .env
git commit -m "Stop tracking .env"
git push
```

- `git rm --cached .env` removes the file from git but **keeps it on your disk**.
- `.gitignore` already lists `.env`, so git will ignore it from now on.
- Important: don't use GitHub's "Add files via upload" with the whole folder
  again. The web upload ignores `.gitignore` — that's how `.env` got in.

Check it worked:

```powershell
git ls-files .env
```

Expected output: **nothing**. If it prints `.env`, it's still tracked.

---

## Step 3. Generate a new JWT secret (`SECRET_KEY`)

This key signs staff login tokens. Whoever has it can forge an admin login.

```powershell
python -c "import secrets; print(secrets.token_hex(64))"
```

- `secrets.token_hex(64)` = 64 random bytes, printed as 128 hex characters.
- Copy the output, open `.env` in Notepad (`notepad .env`), and replace the
  value after `SECRET_KEY=`. Save.

Effect: everyone (admin, kitchen, cashier) gets logged out and must sign in again.
That's expected.

---

## Step 4. Generate a new receipt-signing secret (`RECEIPT_HMAC_SECRET`)

This key proves receipts are real. Whoever has it can forge "paid" receipts.

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Replace the value after `RECEIPT_HMAC_SECRET=` in `.env`. Save.

Effect: receipt QR codes issued **before** this change will no longer verify.
Receipts already expire after 7 days (`RECEIPT_EXPIRY_SECONDS=604800`).

---

## Step 5. Rotate the Chapa key (`CHAPA_SECRET_KEY`)

The leaked key is a **test** key (`CHASECK_TEST-...`), so no real money was at
risk, but rotate it anyway.

1. Log in at https://dashboard.chapa.co
2. **Settings** → **API** (or "API Keys").
3. Regenerate / create a new secret key. Revoke the old one if offered.
4. Paste the new key after `CHAPA_SECRET_KEY=` in `.env`. Save.

When you go live, you'll use the **live** key (no `TEST` in it) from the same page.

---

## Step 6. Change the database password (`DATABASE_URL`)

`DATABASE_URL` contains your PostgreSQL password. The database is on
`localhost`, so outsiders can't reach it today, but change it before you put
the server online.

```powershell
psql -U postgres
```

Then, inside `psql` (replace `food_user` with the username in your `DATABASE_URL`):

```sql
ALTER USER food_user WITH PASSWORD 'paste-a-new-strong-password';
\q
```

- Generate a password with `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
- Put the same new password into `DATABASE_URL` in `.env`.

---

## Step 7. Restart the server

Close the server window, then double-click **Start Server.bat** again.
The server reads `.env` only at startup.

---

## Step 8. Change all staff passwords

The leaked `.env` has the starting passwords for `admin`, `kitchen_staff` and
`cashier1`. Changing them in `.env` does nothing — those values are only used
the very first time the database is created. Change them in the database:

```powershell
cd backend
python change_password.py
```

- Pick an account (1, 2, 3), type the new password twice. Input is hidden.
- Repeat for **all three** accounts.
- Use at least 12 characters, different for each account.
- No restart needed.

Then, in `.env`, replace the three `*_DEFAULT_PASSWORD` values with new random
ones too (`secrets.token_urlsafe(16)`), so the leaked ones are dead everywhere.

---

## Step 9. Production settings (only when you go live)

When the site is on a real server with real Telebirr / CBE Birr / Chapa live keys:

```
ENVIRONMENT=production
PAYMENT_SANDBOX=false
```

- `PAYMENT_SANDBOX=true` (the default) **fakes** Telebirr and CBE Birr payments
  and doesn't send SMS. Fine for testing, never for real customers.
- Don't set it to `false` until you have the live `TELEBIRR_*`, `CBE_BIRR_*` and
  `AFRICASTALKING_*` keys in `.env`, or payments will fail.
- `ENVIRONMENT=production` hides the API docs pages. See `SECURITY.md`.

---

## Step 10 (optional). Erase `.env` from git history

After steps 3–8, the leaked values are useless, so this is cleanup, not the fix.
It rewrites history and needs a **force push**, so do it only when nobody else
has a copy of the repo.

```powershell
pip install git-filter-repo
cd ..
git clone https://github.com/ephreme96/Food-ordering-system.git repo-clean
cd repo-clean
git filter-repo --path .env --invert-paths
git remote add origin https://github.com/ephreme96/Food-ordering-system.git
git push origin --force --all
```

- `git clone` makes a fresh copy (filter-repo refuses to run on a used copy).
- `filter-repo --path .env --invert-paths` = rewrite every commit, dropping `.env`.
- filter-repo removes the `origin` link for safety, so we add it back.
- `--force` replaces GitHub's history with the cleaned one.

After this, delete your old local folder's `.git` history or re-clone, otherwise
a normal `git push` from the old copy could bring `.env` back.

---

## Checklist

- [ ] Repo made private
- [ ] `.env` no longer tracked (`git ls-files .env` prints nothing)
- [ ] New `SECRET_KEY`
- [ ] New `RECEIPT_HMAC_SECRET`
- [ ] New Chapa key, old one revoked
- [ ] New database password
- [ ] Server restarted
- [ ] admin, kitchen_staff, cashier1 passwords changed with `change_password.py`
- [ ] New `*_DEFAULT_PASSWORD` values in `.env`
- [ ] (Go-live) `ENVIRONMENT=production`, `PAYMENT_SANDBOX=false`
- [ ] (Optional) History purged
