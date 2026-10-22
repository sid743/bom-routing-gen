# Hosting on Azure (Azure for Students)

One small always-on VM runs the app 24/7 behind HTTPS with a login.
Every **PC** command below is a single line for **Command Prompt** (edit the names before
pressing Enter). Commands marked **VM** run inside the server after you SSH in.

## Cost (Central India prices, Oct 2026)

| Item | Cost |
|---|---|
| VM `Standard_B2ats_v2` (2 vCPU, 1 GiB, AMD, **Regular** – not Spot) | free: 750 h/month, 12 months, on Azure for Students (otherwise $0.00615/h ≈ $4.50/month) |
| Static public IP (Standard) | $0.005/h ≈ $3.65/month |
| OS disk, Standard HDD 30 GB (S4) | ≈ $1.69/month |
| Outbound traffic | first 100 GB/month free |
| **Total from your $100 credit** | **≈ $5.35/month** |

Azure for Students needs no card, so nothing is charged beyond the credit. When the credit
runs out or the year ends without renewal, the subscription is disabled and the site goes
offline: renew yearly while you're a student and watch the balance in the portal
(**Cost Management + Billing**).

## 0. Put the code on GitHub

Commit this folder: `bom_routing_app.py`, `bom_routing_core.py`, `requirements.txt`,
`.gitignore`, `deploy/`, `README.md`, `DEPLOY.md`. (`bom_routing_data/` is ignored — the
history lives on the server.) A **public** repo is simplest; for a **private** repo see 4b.

## 1. PC — one-time tools

```
winget install --exact --id Microsoft.AzureCLI
```
Close and reopen Command Prompt, then:
```
az login
```
(No separate SSH key step: the VM command in step 3 creates one for you in
`%USERPROFILE%\.ssh\id_rsa`, or reuses it if it already exists.)

## 2. PC — find a region your student account allows

Student subscriptions may only deploy to a few regions. List yours:
```
az policy assignment list --query "[?displayName && contains(displayName,'region')].parameters" -o json
```
Pick one from the list (e.g. `centralindia`). If it prints nothing, look in the portal:
**Policy → Assignments → "Allowed resource deployment regions"**.

## 3. PC — create the VM

Choose a site name for `--public-ip-address-dns-name` (lowercase, unique in the region, e.g.
`bomrouting-sid`). Your address becomes `<name>.<region>.cloudapp.azure.com`.

```
az group create --name bom-routing-rg --location centralindia
```
```
az vm create --resource-group bom-routing-rg --name bom-routing-vm --location centralindia --image Canonical:ubuntu-24_04-lts:server:latest --size Standard_B2ats_v2 --priority Regular --admin-username azureuser --generate-ssh-keys --storage-sku Standard_LRS --public-ip-sku Standard --public-ip-address-dns-name bomrouting-sid
```
```
az vm open-port --resource-group bom-routing-rg --name bom-routing-vm --port 80,443 --priority 900
```
The create output shows `"fqdns": "bomrouting-sid.centralindia.cloudapp.azure.com"` — that's
your address. If it fails:

* `RequestDisallowedByAzure` → region not allowed for your account; use one from step 2
  (change `--location` in both commands).
* `SkuNotAvailable` / capacity error → try another allowed region, or `--size Standard_B1s`
  (also free for students; $0.0112/h after).
* DNS name already used → pick another `--public-ip-address-dns-name`.

## 4. VM — install the app

PC:
```
ssh azureuser@bomrouting-sid.centralindia.cloudapp.azure.com
```
(type `yes` the first time). Now you're on the VM.

**4a. Public repo** — VM (one line; use your repo URL and your address):
```
git clone https://github.com/<you>/<repo>.git bom-routing && cd bom-routing && sudo bash deploy/setup.sh bomrouting-sid.centralindia.cloudapp.azure.com
```

**4b. Private repo** — VM, first make a read-only deploy key:
```
ssh-keygen -t ed25519 -f ~/.ssh/github -N "" && cat ~/.ssh/github.pub
```
Copy the printed line into GitHub → your repo → **Settings → Deploy keys → Add deploy key**
(leave "write access" off). Then:
```
GIT_SSH_COMMAND="ssh -i ~/.ssh/github -o StrictHostKeyChecking=accept-new" git clone git@github.com:<you>/<repo>.git bom-routing && cd bom-routing && git config core.sshCommand "ssh -i ~/.ssh/github" && sudo bash deploy/setup.sh bomrouting-sid.centralindia.cloudapp.azure.com
```

The setup asks for a **site username and password** (what people type in the browser), then
installs everything. When it says *Done*, open `https://bomrouting-sid.centralindia.cloudapp.azure.com`.
The first load can take ~30 s while the HTTPS certificate is issued.

What setup does: Python venv + gunicorn as the `bom-routing` service (restarts on crash,
starts on boot), Caddy for automatic HTTPS (Let's Encrypt) and the login, 1 GB swap, history in
`/var/lib/bom-routing`. It's safe to re-run (e.g. to change the password).

## 5. Updating after you push to GitHub

PC (one line — pulls and restarts on the VM):
```
ssh azureuser@bomrouting-sid.centralindia.cloudapp.azure.com "cd ~/bom-routing && bash deploy/update.sh"
```

## Day-to-day

* **Logs** (VM): `sudo journalctl -u bom-routing -n 50`
* **Back up the history to your PC** (PC):
  `scp -r azureuser@bomrouting-sid.centralindia.cloudapp.azure.com:/var/lib/bom-routing %USERPROFILE%\Desktop\bom-routing-backup`
* **Change the login** (VM): `cd ~/bom-routing && sudo bash deploy/setup.sh <address>`
* **Staying online:** Regular-priority VMs aren't evicted like Spot. Don't turn on the VM's
  *Auto-shutdown*. After Azure maintenance reboots, both services start by themselves.
* **Removing everything** (permanent — back up first): `az group delete --name bom-routing-rg`
