# OwlNest (Property Manager)

OwlNest keeps track of your rental properties. Your records are kept on the app's own server on the internet, so everyone sees the same records. Open it in any web browser, on a computer or a phone, and sign in with your name and password. A computer can also have the app installed, which opens it without signing in.

It keeps your properties and their leases (with walkthroughs and extensions), insurance, every bit of money in and out, and maintenance requests, including those your tenants send from their phones. It reminds you before leases and insurance end, makes a cash-flow report for your accountant, and backs everything up automatically.

---

## Signing in in a web browser

On any computer or phone, open the app's web address (Settings shows it), and sign in with your name and password. Bookmark it to find it again; on an iPhone, tap Share → **Add to Home Screen** to get an icon.

- **You stay signed in** on each computer or phone for 30 days after you last used the app there. On a computer or phone others use, click **Sign out** at the top right when you're done.
- **Forgot your password?** Someone else who can sign in gives you a new one: **Settings → People who can sign in → New password**. A computer with the app installed can do this too.
- **Wrong logins are counted**, by day and by name, at `/settings/failed-logins` (plain text, no page links to it; Larry asks Claude, who reads it with the server's key). The password and any name that doesn't exist are never kept.
- **After 10 wrong passwords in a row**, that name is locked until someone clicks **Unlock** in Settings (or gives it a new password). Devices already signed in keep working, and the Home page shows the lock.
- **To let someone sign in**, click **Add a person** in Settings. Passwords need at least 8 characters; a few words together are hardest to guess. **Remove** stops someone signing in.
- **Lost a phone or computer that's signed in?** Change that person's password in Settings. That signs them out everywhere.

---

## One-time setup

This installs the app on a computer, so it opens with one click and without signing in. A web browser needs no setup. If someone already set up the app for you, skip to [Opening the app](#opening-the-app).

### 1. Install Python 3.12

Python is the free program the app runs on.

1. Go to <https://www.python.org/downloads/> and download **Python 3.12** for your computer.
2. Open the downloaded file and follow the steps.
   - **Windows only:** on the first screen, tick the box **"Add python.exe to PATH"** before you click Install.

### 2. Unzip the app's files and put them somewhere safe

The app's files come as one zip file, **OwlNest.zip**, from whoever looks after the app. **Keep it private:** anyone who has these files can open your records, so don't forward it or put it anywhere others can get it.

1. Double-click the zip file. A folder named **PropertyManager** appears next to it.
2. Move that folder into your home folder. Avoid the Desktop and Downloads folders, where it's easy to delete by accident. Don't move it again after step 3; if you do, run step 3 again.
3. Put the zip file in the Trash.

Your records aren't kept in this folder (they're on the app's server), so it can be replaced later without losing anything.

### 3. Run the setup file

- **Mac:** double-click **Set Up Property Manager.command**.
- **Windows:** double-click **Set Up Property Manager.bat**.

A window opens and shows progress for a few minutes. When it says **Setup finished**, press Return (Mac) or any key (Windows) to close it. You only need to do this once.

Setup puts a **OwlNest** icon (a white owl on a blue square) on the Desktop:

- **Mac:** the app itself is in the **Applications** folder inside your home folder.
- **Windows:** it's also in the Start menu.

**If your Mac says the file "can't be opened"** because Apple can't check it: open **System Settings → Privacy & Security**, scroll down, click **Open Anyway** next to the file's name, and confirm. You only need to do this once for each file.

If your Mac asks whether Terminal may access your Desktop, click **Allow**, so the icon can be put there.

---

## Opening the app

**Double-click the OwlNest icon** on the Desktop. Your web browser opens the app after a few seconds. No other windows appear. The computer needs to be connected to the internet.

- **Mac:** while the app is open, OwlNest shows in the Dock. To keep it there for next time, right-click its Dock icon and choose **Options → Keep in Dock**. Clicking it in the Dock opens the app in your browser again.
- If you close the browser tab by accident, click the icon again. It just opens the app again.
- The first time, your Mac may ask whether OwlNest may access your Documents folder. Click **Allow**, so a copy of your records can be saved there each day (see [Backups](#backups)).

## Closing the app

Click **Close the app** at the top right of any page. Or, on a Mac, right-click OwlNest in the Dock and choose **Quit**.

Everything you entered is saved on the server as you go, so there's no "save before closing" step. Closing the app only closes it on this computer. It's also fine to leave the app open all the time: it closes by itself when the computer shuts down.

---

## Where your records are kept

All your records and files are kept on the app's **server**: a computer on the internet, run by a company named Render, that's always on. Everyone who signs in, and every computer with the app installed, shows and changes the same records. If something was just changed somewhere else, reload the page to see it. (If two people change the same record at the same moment, the one saved last wins.)

Each computer with the app also keeps daily copies of everything, in **Documents → Property Manager Backups**. See [Backups](#backups).

---

## Using the app

The menu at the top leads to the five main screens: **Home**, **Properties**, **Transactions**, **Maintenance** and **Reports**. Notes, Archive, Settings and Help are the smaller links at the top right. The **Help** page answers common questions, also in Simplified Chinese (click **简体中文** at the top of it).

### Home

The Home page starts with a blue band: what your homes are worth (see *Estimated value* below), and buttons to add a transaction or a repair request. Four boxes under it show this month's rent (received, out of the current leases' monthly rent), this year's net cash flow (as in Reports), open repairs, and how many new deposits the latest fetch from the bank brought in. Then come up to six of your properties, the open and in-progress maintenance requests, and what needs attention: new requests from tenants, leases and insurance ending in the next 60 days, leases past their end date, and your own reminders (such as property tax). When something needs attention, a button at the top of every page says so.

### Properties and leases

- **Properties** lists your homes by **LLC**, with the homes in no LLC at the end. **Add LLC** adds one, which is just a name. Choose a home's LLC when you add or edit it. Menus of properties are grouped the same way. A transaction can be for a home, or for an LLC itself, such as its accounting fee.
- Each home has **one current lease**; its earlier leases are under **Previous leases**.
- **Photos:** a property's page shows its photos as a slideshow (swipe on a phone, or use the arrows). Click **Edit photos** at its top right to add photos (iPhone photos are fine, several at once) or remove them; removed photos go to the Archive.
- A property's page shows its bedrooms, bathrooms and square feet, such as "3 beds · 2.5 baths · 1,916 sq ft". Fill them in on its Edit page; for a home whose value is looked up (below), RentCast fills in any that are empty.
- On a property's page, click **Add a lease** and fill in the tenants' names, phone and email, the dates, the rent and the deposit (leave the end date empty for month-to-month).
- Each lease has two **walkthroughs**: **Move-in** (before the tenants arrived) and **Move-out** (after they left), each with a date, notes, and files such as the report and photos.
- **Rent** is a list on the lease's page: each monthly rent and the date it starts. The app uses the one in effect today, so a new rent takes over by itself on its date. Add a rent change, edit one, or remove one there.
- **Rent reminders:** from the day rent is due (each month, on the day of the month the lease started), a reminder shows on Home and at the top of the lease's page until the rent and any late fee are paid in full. Add payments as transactions with the purpose **Rental Income** or **Late Fees** and the day they were really paid; the late fee goes by that date, even if you add it later. Each rent has its own late fee, added once: 5% of the rent when it's 5 days late, unless you change it. The reminder is yellow until the late fee is added, and red from then on. **Waive late fee** and **Waive rest of rent** on the reminder let it go.
- **Extend lease** sets a new end date, can add a new rent with the date it starts, and keeps the signed extension agreement. **End lease** moves it to Previous leases; if that's before its end date, the app warns you first. Ended one by mistake? Open it and click **Revive lease**.
- **Insurance:** add each policy (property, renters, and so on) with its dates and papers. You're reminded 60 days before it ends.
- **Estimated value:** tick **Look up its estimated value each month** on a property's Edit page, and its value is looked up from RentCast about once a month and shown on its page, with the range RentCast is fairly sure of. It needs the full address, including the ZIP code. It's an estimate from online data, not an appraisal.

### Transactions

Every bit of money in or out goes in **Transactions**: rent, repairs, insurance, the mortgage, anything. Choose money in or money out, the amount, the property, and the **purpose** (such as Rental Income or Repair & Maintenance). Money out shows as a negative amount. Leave the tenant empty and it's filled in from the lease. Attach receipts, and link a bill to its maintenance request.

### Maintenance

**Maintenance** lists every request, newest first, with its type, the company fixing it, pictures and receipts, and its status: **Open**, **In progress** or **Closed**. A request's page shows its transactions and what it has cost so far.

### The tenant portal

Each lease has its own link where the tenants can send you repair requests from their phone, with photos, and see their status. On the lease's page, click **Copy link** (to paste into a text) or **Email it to the tenants**. There's no password, so only send it to them. **Make a new link** replaces it if it reaches the wrong person, and it stops working when the lease ends (until you revive the lease). The app itself never sends texts or emails, so check the Home page for new requests.

### Reports

**Reports** shows your cash flow for any dates (it starts with this year): every purpose, even at zero, with totals for income, operating expenses, and mortgage and loan, then your net operating income and net cash flow. Choose all properties, one LLC or one property, by month, by property or by LLC, and it updates as you choose. An LLC's report has its homes' money and the money for the LLC itself. **Download Excel** or **Download PDF** to send it to your accountant. Charts of the same money are underneath: **See charts** jumps down to them.

### Deleting things

**Notes** (at the top right) keeps plain text notes: add one, or archive it.

**Archive** (and **Remove**) buttons hide things without losing them. Archived properties, maintenance requests, transactions and notes are on the **Archive** page. A property's own archived leases, insurance, photos and files are at the bottom of its page, under **Archives**, and files removed from a request or transaction are on its page. Each has **Restore** and **Delete forever** (this can't be undone; earlier backups still have it). Deleting a property forever also deletes its leases, insurance, reminders and photos, but only once its requests and transactions are gone. Restoring a transaction whose request is archived asks first, because it comes back without the link.

**Reminders** can't be dismissed or archived: each stays on Home and at the top of its property's page until it's taken care of (your own marked **Done**, a lease extended or ended, an insurance renewed).

---

## Backups

Your records are backed up in three ways, without you doing anything:

- **On the server, every day.** Each backup is one zip file named with the date and time, for example `backup_2026-09-24_15-10-02.zip`, containing all your records and files. The server keeps **one from every other day for the last 10 days, and one from each of the 5 months before this one**, and deletes the rest.
- **By Render, every day.** Render also takes a snapshot of the server's whole disk every day, and keeps each one for 7 days.
- **On every computer with the app, every day.** While the app is open, it saves a copy of the server's newest backup in the **Property Manager Backups** folder inside your **Documents** folder, and keeps the same ones. So even if the server were lost, your records are safe on your computers. **Settings** shows when each computer last saved one, and the Home page warns if no computer has for a week.

In **Settings** you can:

- See when the last backup was made.
- Click **Back up now** to make an extra backup on the server at any time, for example after entering a lot of information.

If no backup has worked for 7 days, the Home page shows a warning. If a backup fails, a red message appears at the top of every page until one works.

---

## Restoring from a backup

Restoring is done on the server, so ask whoever looks after the app, and tell them roughly when the problem started. The steps are in [Restoring the server from a backup](#restoring-the-server-from-a-backup) below.

---

## Updating the app

Updates happen on the server, so there's nothing for you to do: after an update, the app shows "The app's server is restarting" for a minute or two, and then the new version.

Once in a while, whoever looks after the app may send you new app files. Then:

1. Close the app.
2. Replace the **PropertyManager** folder with the new one from the zip (see [step 2 of the setup](#2-unzip-the-apps-files-and-put-them-somewhere-safe)).
3. Double-click the **Set Up Property Manager** file once more.
4. Open the app as usual.

---

## If something goes wrong

- **"Can't reach your records right now":** check that the computer is connected to the internet, then reload the page. If the internet works and this keeps happening, ask for help.
- **"The app's server is restarting":** wait a minute or two, then reload the page. This happens after an update.
- **"This copy of the app isn't allowed in", or a message that it "isn't connected to the server":** ask whoever looks after the app for new app files.
- **The icon does nothing, or the browser doesn't open:** go to <http://127.0.0.1:8642/> in your browser. If that doesn't work either, double-click **Start Property Manager.command** (Mac) or **Start Property Manager.bat** (Windows) in the app's folder. It starts the app in a window that shows what's happening; take a photo of it and ask for help. Closing that window stops the app.
- **A message says another program is using port 8642:** restart the computer, then open the app again.
- **Red "The last backup didn't work" message:** click **Back up now** in Settings. If it keeps failing, ask for help: the server's disk may be full.
- **"Something went wrong" page:** go back and try again. If it keeps happening, write down what you were doing and the time, and ask for help.

---

## For the person maintaining the code

The app is plain Python: Flask with Jinja templates, SQLite through SQLAlchemy, one CSS file, and one small script (`static/status.js`, for the status menus on the request lists). There's no JavaScript build step. Please ask the project owner before adding any library not in `requirements.txt`.

The full build spec, and the decisions made since it was written, are in [docs/SPEC.md](docs/SPEC.md).

### How it fits together

```
browser (anywhere) ─────────────────HTTPS + sign-in cookie──────────────────► server (Render)
browser ──► relay (run.py, on each computer, 127.0.0.1:8642) ──HTTPS + key──► server (Render)
```

- **The server** (`property_manager/server.py`) is the whole Flask app, run by gunicorn in Docker on Render. Her records are on Render's disk, in `/var/data/PropertyManager/data` (`app.db`, `documents/` for every attached file, `backups/`; `receipts/` and `invoices/` only hold the first design's files). Every request must carry the header `X-Property-Manager-Key` matching the `PROPERTY_MANAGER_KEY` environment variable (a wrong key gets a bare 401, so the relay can explain it), or come from a browser that has signed in; anyone else is sent to `/sign-in`. Only `/health`, `/static/`, `/sign-in` and the tenant portal are open. It makes a backup on every start and once a day, on Pacific time (`TZ` in the `Dockerfile`).
- **The relay** (`property_manager/relay.py`, started by `run.py`) is what runs on each computer. It does the same browser checks as before (Host must be 127.0.0.1 or localhost; forms must come from the app's own pages), then passes each request to the server with the key, over HTTPS checked with certifi's certificate list. The key never reaches the browser. It answers `/health` itself, and "Close the app" stops it. Once a day it downloads `/settings/backups/newest` into `Documents/Property Manager Backups`, keeps the same ones the server does there, and reports it to `/settings/backups/copied` (table `backup_copies`), so the server can warn when no computer has saved a copy for 7 days.
- **`connection.json`**, in the code folder, holds the server's address and the key. `make_app_package.py` writes it and puts it in the app files. It's ignored by git; never commit it.
- **Signing in** (`property_manager/accounts.py`, `views/sign_in.py`, `views/people.py`): table `accounts` holds each person's name (unique, ignoring capitals) and a Werkzeug scrypt password hash; passwords need 8+ characters. Signing in makes a random 43-character code, kept in the browser's `signed_in` cookie (HttpOnly, Secure, SameSite=Lax, 30 days) and, as a SHA-256 hash, in table `sign_ins` (one row per device). Each sign-in lasts 30 days from its last use, renewed at most hourly (`security._renew_sign_in`). Signing out deletes the row; a new password deletes that person's other rows; 10 wrong passwords in a row lock that name (`accounts.locked_at`) until Unlock in Settings or a new password, with an alert on Home. An unknown name takes as long to refuse as a wrong password. The browser's forms must come from the app's own pages (Origin/Referer), as on the relay. Flask's own session cookie only carries the "Saved." messages, so its key is still random at each start. People are managed in Settings, by anyone who can sign in or a computer with the app; on one computer (`--local`) there's no signing in.
- **The app files** are still a way in: having them is what lets a computer in without signing in. That's why they're sent privately, and why the key is changed if a computer is lost.
- **The tenant portal** (`property_manager/views/portal.py`, at `/portal` on the server) is the one part of the server that's open without the key. Each lease gets a link when it's made, `/portal/link/<token>`, with a random 43-character token in `leases.portal_token` (see `leasing.py`). Opening it sets an HttpOnly, Secure, SameSite=Lax cookie (path `/portal`) that lasts about a year. Every portal page checks the cookie: an unknown token gets "This link isn't working", an ended lease gets the "sorry" page, and reviving the lease brings the same link back. Tenants see only their lease's requests (with status) and her contact details, and can send requests with up to 10 photos (photos only, redrawn as JPGs so GPS details are dropped; at most 5 requests a day per lease). Portal pages send a strict Content-Security-Policy (no scripts), `Cache-Control: no-store`, and refuse forms from other sites. The links use the server's address from Render's `RENDER_EXTERNAL_URL` (or `PROPERTY_MANAGER_PUBLIC_URL`).
- **The redesign (Oct 2026):** the client asked for five screens and simpler records. The first design (invoices, payments, expenses, repairs, vendors, tenants, units, the bank file, the Excel import and export) is kept in git on the branch `archive/v1`. Database version 8 replaced a first-design database with the new, empty tables after the usual startup backup, keeping only her details from Settings.
- **`run.py --local`** still runs the whole app on one computer, the old way, for development (see [Trying it on this computer](#trying-it-on-this-computer)).

### The server

**Setting it up (once):**

1. Put this code in a **private** GitHub repository and push it. (Her spreadsheets and `connection.json` are ignored by git, so they stay on this computer.)
2. Make an account at [render.com](https://render.com) and add a payment card.
3. In Render, choose **New → Blueprint**, connect GitHub, and pick the repository. Render reads `render.yaml`: a web service named `property-manager` in Oregon, with a 5 GB disk. It costs about $7 a month for the server and $0.25 per GB a month for the disk ($1.25 for 5 GB). Click **Apply** and wait until the service says **Live**.
4. The service's page shows its address, such as `https://property-manager-ab12.onrender.com`. On its **Environment** page, reveal and copy `PROPERTY_MANAGER_KEY` (Render made a random one).
5. Make the app files:
   ```
   .venv/bin/python make_app_package.py --server https://property-manager-ab12.onrender.com --key THE-KEY
   ```
   This checks that the server accepts the key, saves `connection.json` here (so this computer's app connects too), and makes `app-package/OwlNest.zip`.
6. Send the zip **privately** (AirDrop, a USB stick, or a private message; not a shared link) to each computer that may use the app. There, follow [One-time setup](#one-time-setup).

**Updating it:** commit and push to GitHub. Render rebuilds and restarts the server, which takes a few minutes; meanwhile the app shows "The app's server is restarting". The server makes a backup on every start, then upgrades the database if the tables changed. The computers only need new app files if `run.py`, `install_launcher.py`, the setup files, or `property_manager/relay.py` changed: run `make_app_package.py` again (no options needed) and send the new zip.

**The bank (Plaid):** her bank's activity, money in and out, comes from [Plaid](https://plaid.com)'s free Trial plan, which can only read: it can't move money. To turn it on:

1. Create a new account on [Plaid's dashboard](https://dashboard.plaid.com) and choose the free Trial. Don't apply for full Production access: that would end the Trial.
2. On Render's Environment page, add `PLAID_CLIENT_ID` and `PLAID_SECRET` (the Production secret). Don't send them in a message.
3. She clicks **Connect the bank** on the **From the bank** page, signs in on her bank's own page, and chooses the accounts to share. Her bank password never goes in this app, or to anyone.

The connection's key is saved in `bank-link.json` in the data folder, which isn't in the database or any backup.

The Trial allows 10 connections, ever (removing one doesn't give it back), so the app only starts a new one when there isn't one. **Reconnect the bank** repairs the same connection, which Bank of America requires once a year; Home reminds her 30 days ahead. Plaid doesn't say how long the Trial lasts, so if it ends, the app stops checking and Home says so.

The server fetches once a day (every hour right after connecting, while Plaid gathers its history), and never more than 24 times in 24 hours. Settings has a **Fetch now** button. Entries wait on the **From the bank** page, where they can't be edited. **Enter into Transactions** opens the transaction form with the date, money in or out, amount and details (payment method and memo) filled in, and the property, purpose and request left to choose. Once saved, the entry stays listed, marked **In Transactions**, and the transaction shows a **BOFA** tag. That transaction can't be archived. **Return to From the bank** deletes it, and its entry can be entered again. An entry can also be **Linked with AI** to a transaction she typed in herself (2026-11-05): several entries can link to one transaction (rent paid in halves), **Unlink** undoes it for good, and archiving that transaction unlinks its entries for good too. The **Accounts** button lists the accounts connected. **Add account** asks which login the account is under. The same login reopens the bank's page to choose accounts on that connection, which doesn't use up another of the 10, and any account left unchosen there stops being shared. A different login needs its own connection, which uses one. An account both logins can see only comes in once. No page disconnects anything: that's done by hand, in the service's **Shell** page on Render: `python -m property_manager.bank_by_hand list` shows each connection's key and accounts (never its access token), and `python -m property_manager.bank_by_hand disconnect KEY` ends one (use `first` for the one with an empty key). Its entries stay on From the bank. On Plaid's free plan, a removed connection doesn't come back, so the file keeps a count of connections ever made. A login connected again gives its money new ids; anything another connection already brought in (same account, day, amount and words) isn't saved twice, so its first pull only adds older months.

To try it with Plaid's made-up bank, run the app on a computer with `PLAID_ENV=sandbox` and the Sandbox secret. Real banks are only ever checked on the server.

**Home values (RentCast):** each home's estimated value comes from [RentCast's API](https://developers.rentcast.io). To turn it on, make an API key in your RentCast account's API section, then on Render's **Environment** page add `RENTCAST_API_KEY` with the key and save (the server restarts). Lookups start about an hour later. RentCast's free plan includes 50 lookups a month, charges for each one after that, and can't stop by itself, so the app (`property_manager/home_values.py`) keeps count:

- only the server looks values up, and only for properties with **Look up its estimated value each month** ticked, that aren't archived, and whose address has a ZIP code;
- a home is looked up again 32 days after its last lookup (RentCast's month starts on your billing day, and 32 days apart means never twice in one of its months), a week later if a lookup failed, or right away if its address changed;
- never more than 40 lookups in any 31 days, which leaves 10 for trying things on RentCast's website (those count too);
- each lookup is saved before it's sent, and counts even if it failed;
- if RentCast doesn't accept the key, lookups stop until the key is changed.

**Settings → Home values** shows how many lookups were made in the last 31 days, and which homes are skipped for lacking a ZIP code. The server checks for due homes once an hour, and the first check comes an hour after each restart. To run that check right away, under the same rules, send a POST to `/settings/home-values/look-up` with the `X-Property-Manager-Key` header; it answers with what it looked up. No page links to it. RentCast's own dashboard shows its count, and it emails at 85% and 100% of the 50.

**Logs:** the service's **Logs** page shows each startup's backup, each home-value lookup, and any errors.

**Disk space:** the Home page warns when the disk is 85% full. To make it bigger, open the service's **Disks** page and raise the size. It can grow but never shrink, and growing it doesn't stop the server.

### If a computer is lost (changing the key)

If a phone or computer that only **signed in** goes missing, change that person's password in Settings; that signs them out everywhere. Nothing else is needed.

Anyone with the **app files** can open her records, so if a computer with the app installed (or a copy of the zip) goes missing, change the key:

1. `.venv/bin/python make_app_package.py --new-key` prints a new random key.
2. On Render, set `PROPERTY_MANAGER_KEY` to it on the **Environment** page and save. The server restarts, and the old key stops working.
3. `.venv/bin/python make_app_package.py --server https://property-manager-ab12.onrender.com --key THE-NEW-KEY`, then send the new zip to the computers you still trust. On each one, close the app and replace its `connection.json` with the one in the new zip (or replace the whole folder and run setup again).

### Restoring the server from a backup

**From Render's daily snapshot** (the last 7 days): on the service's **Disks** page, restore a snapshot. Everything saved after it is lost, but the copies on her computers stay.

**From a backup zip** (from the server's `backups/` folder, or from a computer's `Documents/Property Manager Backups`):

1. Ask everyone to close the app until you're done.
2. If the zip is on a computer, upload it. First add your SSH key in Render (**Account Settings → SSH Public Keys**). Then, with the SSH address from the service's **Connect → SSH**:
   ```
   scp -s backup_2026-11-01_09-00-00.zip srv-abc123@ssh.oregon.render.com:/var/data/
   ```
3. On the service's **Shell** page, unzip the backup, put the current records aside (nothing is deleted), and move the backup's records in. Run these one at a time, with your zip's name and today's date:
   ```
   cd /var/data/PropertyManager
   python -m zipfile -e /var/data/backup_2026-11-01_09-00-00.zip restored
   mkdir before-restore-2026-11-02
   mv data/app.db data/receipts data/invoices data/documents before-restore-2026-11-02/
   mv restored/* data/
   ```
   If a line shows an error, stop and don't restart: nothing has changed before the `mv` lines. (For a zip already on the server, use `data/backups/` and its name instead of `/var/data/`.)
4. Restart the server: **Manual Deploy → Deploy latest commit**. Then open the app and check the records.

### Moving the server to a new address

Render can't change a service's `onrender.com` address, so a new address means a new service. The old one then passes everyone on, so tenants' links keep working.

1. Create the new service from a Blueprint file (such as `render.yaml`), with the same values as the old service's **Environment** page. Using the same `PROPERTY_MANAGER_KEY` keeps `connection.json` and the installed apps' key valid.
2. On the old service, set `PROPERTY_MANAGER_MOVED_TO` to the new address. It restarts, stops changing anything on its disk, and passes every page on (`moved_app` in `server.py`).
3. Copy everything in `/var/data` from the old disk to the new one over SSH, including `bank-link.json`, which is in no backup. Losing that file would mean connecting the bank again, using up more of Plaid's 10 connections. Never print it.
4. Restart the new service and check it. To undo the move, remove `PROPERTY_MANAGER_MOVED_TO` from the old service: its disk is as it was.

### How the icon works

`install_launcher.py` (run by the setup files) makes the icon:

- **Mac:** `~/Applications/OwlNest.app` (named by `APP_NAME` in `config.py`), a "stay open" AppleScript app built with `osacompile` (part of macOS, so no extra library), plus a Desktop symlink.
  - Opening it runs `run.py --background` from the code folder, which starts the relay.
  - Quitting it (Dock → Quit, or computer shutdown) POSTs to `/shutdown`.
  - Every 10 seconds it checks that the relay is still running, and quits itself after the browser's Close button stops it.
- **Windows:** Desktop and Start-menu shortcuts that run `.venv\Scripts\pythonw.exe run.py --background` (no console window).
- **No window:** with `--background`, output goes to `logs/property-manager.log` in the code folder, which is trimmed when it passes 1 MB. Startup problems are shown in a pop-up dialog.
- **Troubleshooting:** the `Start Property Manager` files still run the app in a visible window.

### Layout

| Path | What it is |
| --- | --- |
| `run.py` | Starts the relay on 127.0.0.1 and opens the browser. With `--local`, runs the whole app on this computer instead (with a daily backup while open). |
| `make_app_package.py` | Makes the app files to send to a computer (`app-package/OwlNest.zip`), with `connection.json`. `--new-key` prints a new key. |
| `Dockerfile`, `render.yaml`, `requirements-server.txt` | How Render builds and runs the server: Python, fonts for the report PDFs, the time zone, gunicorn, and the disk. |
| `install_launcher.py` | Builds the one-click icon (see above) and draws `static/icon.png` / `icon.ico`. |
| `property_manager/__init__.py` | `create_app()`: builds the Flask app. With `server_key`, it's the server. |
| `property_manager/server.py` | Builds the app on the server, for gunicorn: checks the key and data folder are set, starts the hourly checks. |
| `property_manager/relay.py` | The app on each computer: passes requests to the server with the key, reads `connection.json`, and copies the newest backup once a day. |
| `property_manager/startup.py` | Startup order: create folders → create database if new → backup → upgrade tables. Also `backup_if_due()` and the hourly check for a daily backup. |
| `property_manager/config.py` | Data folder location, host, port, and the server's key settings. |
| `property_manager/models.py` | All database tables. |
| `property_manager/database.py` | Engine, `db_session`, schema version, and upgrade steps. |
| `property_manager/backup.py` | Backup zips, which ones to keep (one from every other day for 10 days, and one from each of the last 5 months), and the second backup folder (not used on the server). |
| `property_manager/categories.py` | The client's list of purposes (for transactions) and request types, in groups: income, operating expenses, mortgage & loan, transfers. |
| `property_manager/leasing.py` | Lease rules: one current lease per property, portal links, extending, ending (early), reviving, the rent list. |
| `property_manager/bank_link.py` | Her bank's activity through Plaid: connecting, staying on the free plan, fetching once a day, reading the bank's words, and spotting money already typed in. |
| `property_manager/rent_ledger.py` | Whether each month's rent is paid, and its late fee, worked out from the rent list and the transactions. |
| `property_manager/attachments.py` | Files attached to leases, walkthroughs, extensions, insurances, requests and transactions (checked by `receipts.py`, saved in `documents/`). |
| `property_manager/cash_flow.py` | The cash-flow report's numbers. The Reports screen, the Excel file and the PDF all use it. |
| `property_manager/report_files.py` | The cash-flow report as an Excel file (with a sheet of its transactions) or a PDF (landscape, sized to fit). |
| `property_manager/deleting.py` | "Delete forever" from the Archive: what goes with each kind of record, and what's kept. |
| `property_manager/records.py` | Small rules shared by screens (unique names, a property's current and previous leases, the lease on a date, a request's cost). |
| `property_manager/receipts.py` | Checking uploaded files: PDFs kept as they are; photos (and iPhone HEIC) turned upright, shrunk, redrawn as JPG; unique file names. |
| `property_manager/alerts.py` | The "Needs attention" list and the count in the header (new tenant requests, leases and insurances ending, reminders, backups, the server's disk space, copies on the computers). |
| `property_manager/money.py` | Parsing and formatting money (always whole cents). |
| `property_manager/forms.py` | Reading form fields, with plain-words error messages. |
| `property_manager/security.py` | Who may come in on the server: the key, or a signed-in browser (the tenant portal, `/health`, `/static` and `/sign-in` are open); renewing sign-ins; and the browser checks (Host and form origin) used on her computer. |
| `property_manager/accounts.py` | Signing in: password rules and hashes, wrong-password pauses, sign-in codes and their cookie. |
| `property_manager/views/` | One file per screen (Flask blueprints). `portal.py` is the tenant portal; `files.py` adds, opens and removes attached files; `sign_in.py` and `people.py` are the sign-in page and Settings' "People who can sign in". Add new screens to `NAV_ITEMS`. A screen file must not share a name with a file in `property_manager/` (a test checks). |
| `property_manager/templates/` | Page templates. `_macros.html` has form fields that show errors under each field. |
| `templates/_status.html`, `static/status.js` | A request's colored status tag, and the same tag as a menu on the Maintenance list and Home. The script saves her pick at once, fades out a request the list no longer shows, and offers Undo. |
| `templates/help.html`, `help_zh.html` | The Help page in English and Simplified Chinese. Change both together; a test checks they have the same sections. The Chinese version quotes each button's English label exactly, since the menus and buttons are in English. |
| `tests/` | pytest tests. |

### Running the tests

```
.venv/bin/python -m pytest          # Mac
.venv\Scripts\python -m pytest      # Windows
```

Tests always use a temporary data folder and never touch real records.

### Trying it on this computer

**The whole app, the old way** (no server):

```
PROPERTY_MANAGER_DATA=/tmp/pm-dev .venv/bin/python run.py --local
```

**A test server, with the relay in front of it**, using Docker (the same image Render builds). Open `http://localhost:8700` in a browser to try signing in (add a person first through the relay, in Settings; browsers accept the Secure cookie on `localhost`):

```
docker build -t property-manager .
docker run --rm -p 8700:10000 -e PROPERTY_MANAGER_KEY=any-test-key-of-32-or-more-characters -v /tmp/pm-server:/var/data property-manager
```

Then, in another window, point the relay at it with a separate connection file, so the real `connection.json` isn't replaced:

```
export PROPERTY_MANAGER_CONNECTION=/tmp/pm-test-connection.json
.venv/bin/python make_app_package.py --server http://127.0.0.1:8700 --key any-test-key-of-32-or-more-characters
PROPERTY_MANAGER_PORT=8766 PROPERTY_MANAGER_SECOND_BACKUP=/tmp/pm-copies .venv/bin/python run.py
```

(Plain `http://` is only allowed for a server on this same computer; real servers must use `https://`.)

These environment variables change where and how the app runs:

| Variable | Effect |
| --- | --- |
| `PROPERTY_MANAGER_DATA` | Use this data folder instead of `~/PropertyManager/data`. On the server, it's set in the `Dockerfile`. |
| `PROPERTY_MANAGER_KEY` | The server's secret key (on Render, set on the Environment page). |
| `PLAID_CLIENT_ID`, `PLAID_SECRET` | Plaid's keys, to bring in her bank's activity (on Render, set on the Environment page). Without them, the bank isn't checked. |
| `PLAID_ENV` | Set to `sandbox` to use Plaid's made-up bank, which also works on a computer. Otherwise it's Plaid's real system, on the server only. |
| `RENTCAST_API_KEY` | The RentCast API key for each home's estimated value (server only; on Render, set on the Environment page). Without it, no values are looked up. |
| `PROPERTY_MANAGER_CONNECTION` | Use this connection file instead of `connection.json` in the code folder. |
| `PROPERTY_MANAGER_PUBLIC_URL` | The server's address for tenants' portal links, such as a web address of her own. Without it, Render's `RENDER_EXTERNAL_URL` is used. |
| `PROPERTY_MANAGER_PORT` | Use this port instead of 8642. |
| `PROPERTY_MANAGER_NO_BROWSER` | If set, don't open a browser window (or show pop-up messages). |
| `PROPERTY_MANAGER_SECOND_BACKUP` | Save backup copies here instead of `Documents/Property Manager Backups` (the relay's daily copies, or the second copy with `--local`). When `PROPERTY_MANAGER_DATA` is set, the default is a folder next to that data folder, so testing never writes into the real Documents folder. |

To try the Mac icon without touching the real app, build it somewhere else with test settings:

```
.venv/bin/python install_launcher.py --destination /tmp/pm-app --no-desktop-shortcut \
  --env PROPERTY_MANAGER_CONNECTION=/tmp/pm-test-connection.json --env PROPERTY_MANAGER_PORT=8766
```

### Rules the code follows

- **Money** is stored as whole cents (integers) in columns ending in `_cents`. Use `parse_money()` and `format_money()`.
- **Dates** are stored as `YYYY-MM-DD`.
- **Archive first, then delete forever.** "Archive" sets `archived_at`; the Archive page clears it (Restore) or removes the record and what belongs to it (Delete forever, in `deleting.py`). The only other deletions are small corrections on a lease's page: removing a row from its rent list, and undoing a waiver.
- **No code deletes files in the data folder**, except `backup.remove_old_backups()`, which removes backup zips the rule doesn't keep (in `data/backups`, the second backup folder, and each computer's copies folder). Even "Delete forever" leaves attached files in `documents/`. Uploads waiting in the system's temporary folder are removed from there once saved.
- **Property names are unique**, ignoring upper/lower case, so drop-down lists are never ambiguous.
- **Money signs:** a transaction's `amount_cents` is positive for money in and negative for money out, so totals are plain sums. Its `category` is a name from `categories.py`; a transaction linked to a request has the request's category and property (`records.follow_request`).
- **Changing tables:** raise `SCHEMA_VERSION` in `database.py` and add a matching function to `UPGRADE_STEPS`. On startup, the app backs up first, then runs each missing step in its own transaction. It refuses to upgrade if the backup failed, and refuses to open a database from a newer version of the app.
- All state-changing requests are POST forms. On each computer, the relay (or the app, with `--local`) rejects requests whose Host isn't `127.0.0.1` or `localhost`, and POSTs whose Origin or Referer comes from another site. The server sends every request without the key or a sign-in to the sign-in page, except `/health`, `/static/`, and the tenant portal (which checks each tenant's link itself). It refuses forms from other sites for signed-in browsers, the sign-in page and the portal.
