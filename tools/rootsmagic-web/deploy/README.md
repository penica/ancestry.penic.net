# Deploy RootsMagic Reader on Linux

The reader is a private browser and API for published copies of the RootsMagic tree. RootsMagic on the Mac is where edits are saved. The Linux reader opens its local SQLite snapshot in read-only mode and serves linked files from the NAS.

## Paths and services

| Location | Purpose |
| --- | --- |
| `/home/rootsmagic-reader/RootsMagicReader/` | SSH preview and release staging |
| `/srv/rootsmagic-reader/releases/` | Versioned application releases and Python environments |
| `/srv/rootsmagic-reader/current` | Link to the active release |
| `/srv/rootsmagic-reader/trees/` | Published `.rmtree` files listed by the Tree selector |
| `/srv/rootsmagic-reader/incoming/` | Private staging for a publication in progress |
| `/srv/rootsmagic-reader/backups/` | Prior snapshots for rollback |
| `/srv/rootsmagic-reader/state/accounts.sqlite3` | VM accounts, tree access, invitations, and sessions |
| `/srv/rootsmagic-reader/state/comments.sqlite3` | Reader comments, research follow-ups, and shared chart views |
| `/srv/rootsmagic-reader/state/settings.json` | Saved tree, chart, security, and backup settings |
| `/srv/rootsmagic-reader/state-backups/` | Private daily account, comment, and encryption-key backups |
| `/srv/rootsmagic-reader/backup-status/latest.json` | The last verified backup’s timestamp and included file types |
| `/etc/rootsmagic-reader/mfa.key` | Root-owned key protecting authenticator secrets |
| `/mnt/archive/Books/` | Existing NAS book archive |
| `/mnt/archive/RootsMagicReader/Media/` | NAS copies of linked files from the Mac |
| `/mnt/archive/RootsMagicReader/PreviousMedia/` | Previous copies of files updated by Publish |
| `/etc/rootsmagic-reader/reader.env` | Production tree, host, media, and PDF font settings |

The `rootsmagic-reader.service` systemd unit runs as `rootsmagic-reader` and listens on `127.0.0.1:8765`. Nginx serves `reader.example.org` and the LAN address `lan-reader.example.org` over HTTPS. Replace the example hostnames with your own. Place any upstream proxy and firewall in the intended network path. The installed unit and Nginx site are based on the templates in this folder.

`ROOTSMAGIC_DB` selects the published working tree and `ROOTSMAGIC_TREE_DIR` sets the folder scanned for additional files. `ROOTSMAGIC_DEFAULT_TREE_SLUG` and `ROOTSMAGIC_START_PERSON` preserve the initial tree identity and starting person. An Administrator can change the default from the published catalog in **System settings → Trees and chart**. The reader preserves each account’s tree permissions.

## VM accounts

**Reader** accounts view their assigned trees, including associated records, images, and PDF exports. **External Reader** limits an assigned tree to publicly recorded vital facts and family links for deceased people. The same policy applies to search, records, shared charts, and PDFs. Notes, comments, citations, and media are reserved for family access. An **Administrator** can open every tree and use **Manage accounts** to invite users, change tree access, revoke an invitation, or disable an account. Each invitee sets their own password through a one-time link. Changes to account access take effect in open sessions. See [External Reader access](../README.md#external-reader-access) for the full policy.

Open **Account → System settings** to reach **Manage accounts** or **Email server**. Choose **STARTTLS**, **TLS**, or **None** under **Encryption** to set the port to 587, 465, or 25 respectively. You can edit the port after choosing the mode. **None** sends mail over an unencrypted SMTP connection. Choose **Enabled** under **SMTP authentication** for a server that requires a username and password, or **Disabled** for a relay that accepts mail without a login. Use **Send test email** to try the settings in the form before saving. Re-enter a newly typed password when saving after a test. Saved passwords are encrypted with the server credential key and can be reused if you re-enable authentication for the same server and username. Invitation delivery uses the saved settings, and each new invitation has a copyable link.

Signed-in users manage their name, username, contact email, and password under **Account settings**. Changes to username, email, and password require the current password and replace existing sessions. The contact email is stored as profile information. **My person** links the account to a record in an assigned tree and adds a shortcut in that tree's reader header. Profile and shortcut changes are stored in the VM account database.

The production service uses `ROOTSMAGIC_ACCOUNTS_DB` and `ROOTSMAGIC_PUBLIC_ORIGIN`. The public origin is `https://reader.example.org` and is used for invitation links. Set `ROOTSMAGIC_ADDITIONAL_ORIGINS` to `https://lan-reader.example.org` and allow both hostnames in `ROOTSMAGIC_ALLOWED_HOSTS`. Create the state directory as `rootsmagic-reader` with mode `0700` when setting up the VM. Keep the account database on the VM with the other service state. The Mac **Publish** workflow continues to send tree snapshots independently of account data.

The service sets `ROOTSMAGIC_REQUIRE_ACCOUNTS=1` in its launch command. The reader validates the account database and HTTPS origin before opening the listener. If the database setting is blank, the file is unavailable, or its schema needs migration, check the service journal, correct `/etc/rootsmagic-reader/reader.env` or migrate the database, and restart the service.

The first comment creates a private SQLite database beside the VM account database. Include it in state backups. Set `ROOTSMAGIC_COMMENTS_DB` to override its path. Comments stay on the reader that created them and survive publication of a new snapshot with the same tree slug and person IDs.

The daily `rootsmagic-reader-state-backup.timer` runs SQLite’s online backup. **System settings → Backups** shows the last verified run and sets how many snapshots to keep, with a default of 14. Run `sudo systemctl start rootsmagic-reader-state-backup.service` to take one immediately. Each snapshot includes the account database, the encryption key, and comments and saved reader settings once created. The app can read the status file. The backup files are private to the VM operator. Copy a selected backup to an isolated preview to check recovery. A restored reader must use the corresponding account database and key together, and retain the tree slugs used by the comments.

Set `ROOTSMAGIC_SETTINGS_FILE` to an absolute private JSON file to override the saved settings location. Keep `ROOTSMAGIC_BACKUP_STATUS_FILE` consistent between the reader and the backup service. The default status directory is owned by root and readable by the reader group.

Before updating an existing VM, back up the account database and run `.venv/bin/python reader_accounts.py migrate /srv/rootsmagic-reader/state/accounts.sqlite3` as `rootsmagic-reader` from the tested release. The migration creates the API key table, adds missing account fields and tables, and enables the External Reader role in invitations and tree memberships. Existing accounts, access, sessions, and invitations are preserved. Restart the service with the updated release after migration.

API key metadata and hashes are included in the account database backup. After restoring an older backup, review **System settings → API keys** and revoke any credentials the backup brought back. The proxy must pass the HTTPS scheme and `Authorization` header to the reader for [registry integration](../README.md#registry-integration-api).

For a new VM, run this over SSH as `rootsmagic-reader` to create the first administrator's setup invitation. Copy the output privately into the `token` query parameter of `https://reader.example.org/invite`. The setup link expires after 30 minutes. Run the command again after expiry to issue a fresh link.

```sh
cd /srv/rootsmagic-reader/current
.venv/bin/python reader_accounts.py bootstrap-invite /srv/rootsmagic-reader/state/accounts.sqlite3 admin
```

If an account holder forgets their password, run `reader_accounts.py reset-password /srv/rootsmagic-reader/state/accounts.sqlite3 USERNAME` with the release's Python interpreter over SSH as `rootsmagic-reader`. The command prompts for the password and ends that user's current sessions.

### Two-step verification

Users can turn on **Two-step verification** under **Account settings**. The reader generates a QR code and a manual setup key. A valid code from the authenticator finishes enrollment. Save the recovery codes at setup. Each recovery code can be used once. Turning off verification or replacing recovery codes requires the current password and a verification code.

The production systemd unit supplies `/etc/rootsmagic-reader/mfa.key` as a service credential. Keep this root-owned file at mode `0600` and back it up securely alongside the account database. The key is generated once per VM. Store and restore the key together with the corresponding account state. [systemd credentials documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#Credentials)

If a user loses both the authenticator and every recovery code, the VM operator can verify the user's identity and run `reader_accounts.py reset-mfa /srv/rootsmagic-reader/state/accounts.sqlite3 USERNAME` with the release's Python interpreter over SSH as `rootsmagic-reader`. The reset removes the factor and revokes that account's sessions. The user can sign in and enroll a new authenticator.

### Passkeys

Passkeys use WebAuthn on the canonical HTTPS origin, `https://reader.example.org`. Set up, name, or remove a passkey in **Account settings → Passkeys**. Changes require the current password and any configured second factor. Removing a passkey also signs out the account's other sessions. The LAN hostname uses password sign-in and the existing two-step verification.

### Sign-in and export limits

Administrators can set the password and second-factor attempt allowances and the per-account PDF export allowance in **System settings → Security and limits**. The reader also limits sign-in attempts by visitor address. On the public proxy host, Nginx Proxy Manager uses `CF-Connecting-IP` only for connections from Cloudflare's published IP ranges and replaces `X-Real-IP` with the verified address. Uncomment `set_real_ip_from` in the public virtual host and set the exact upstream proxy address after verification. The VM accepts that header only on the public hostname and only from Nginx Proxy Manager's exact address. It rebuilds `X-Forwarded-For` with the resulting address. The LAN hostname uses its immediate peer. Keep the proxy address in `ROOTSMAGIC_SHARED_PROXY_ADDRESSES` so account limits still apply if the only address available is the proxy's own address. After changing either proxy, verify the public address and repeat a direct-origin request with forged `CF-Connecting-IP`, `X-Real-IP`, and `X-Forwarded-For` headers. PDF requests have chart and body limits and a shared render limit.

Security events use the `reader_audit` prefix in the service journal. They record the event, outcome, and internal account or invitation IDs. Check them with `sudo journalctl -u rootsmagic-reader.service --grep reader_audit`.

For future Contributor and Editor roles, proposed changes will be staged on the VM. The administrator will approve proposals, then review a comparison on the Mac before applying changes to the working RootsMagic tree and publishing a fresh snapshot.

## Private preview

Start the preview in an SSH terminal on the VM. It uses its own SQLite copy in the work folder.

```sh
cd "$HOME/RootsMagicReader/preview/app"
set -a
. ../reader.env
set +a
../.venv/bin/python -m uvicorn serve:app --host 127.0.0.1 --port 18765 --no-proxy-headers
```

Forward the port from a separate terminal on the Mac.

```sh
ssh -N -L 127.0.0.1:18765:127.0.0.1:18765 rootsmagic-reader@lan-reader.example.org
```

Open the preview while both commands are running. [SSH preview](http://127.0.0.1:18765/pedigree)

## Publish a tree update

Save in RootsMagic first. Open the working tree in the Mac reader and click **Publish** beside **Refresh**. Start the Mac reader with `ROOTSMAGIC_ENABLE_PUBLISH=1` to show the button. The preview lists changes to the compared record tables and shows new, updated, reusable, and unavailable media. Click **Publish to VM** after reviewing the preview. Open a fresh preview if the saved tree or its linked media changes. After publication, check the accepted filename, media counts, and previous snapshot filename. Click **Refresh** in the VM reader.

To run the same publication from the Mac terminal, use Python 3.12 or newer and a verified SSH connection. Set `ROOTSMAGIC_PUBLISH_SERVER` in the Mac reader’s launch environment to use the browser button.

```sh
python3 tools/rootsmagic-web/deploy/publish-tree.py --with-media \
  --server rootsmagic-reader@lan-reader.example.org \
  "$HOME/Projects/FamilyTrees/family-tree.rmtree"
```

Add `--preview` to compare the saved tree and linked media. Repeat previews reuse verified checksums for unchanged managed NAS files. Use `--preview --verify-media` to read every managed NAS file again. Pass the returned `plan` value as `--expect-plan <value>` to publish that reviewed snapshot. Confirmation and installation check media contents directly.

The publisher takes one SQLite snapshot, verifies or copies the media it references, then sends that same snapshot over SSH. It reuses books already in the NAS archive and copies Mac files into the dedicated NAS media directory. The VM checks the snapshot checksum and SQLite contents, saves the previous snapshot in `backups/`, and atomically replaces the file in `trees/`. The default destination name follows the source filename with Unicode normalized for Linux. Use `--name 'family-tree.rmtree'` when publishing a differently named copy as the working tree. Publish additional trees under their own names.

Click **Refresh** in the reader after a successful publication. Reload the browser tab when adding a tree so the Tree selector reloads the file list. Publication is on demand. A failed check leaves the published tree available. If Publish reports a missing file, check the media link in RootsMagic and save before retrying. If SQLite sidecar files are reported beside a published tree, inspect how that file was placed there before retrying. If SSH host verification fails, confirm the VM host key through a trusted channel.

## Media and PDF export

Set `ROOTSMAGIC_MEDIA_MAP` in the service environment as a JSON object. The keys are normalized absolute directories recorded in RootsMagic, with forward slashes. The values are absolute Linux directories. The longest matching prefix wins.

```ini
ROOTSMAGIC_MEDIA_MAP='{"/Volumes/home/FamilyArchive/Books":"/mnt/archive/Books","/Volumes/home-1/FamilyArchive/Books":"/mnt/archive/Books"}'
```

Before enabling media publication, adapt the sample project and archive directories in `publish_media.py` on both the Mac and the VM. Use matching destinations in `ROOTSMAGIC_MEDIA_MAP`. The publisher preserves exact recorded filenames, including Unicode spellings. Keep the NAS mount available for publication and serving linked files.

The production environment configures separate font files for regular, bold, and title text. Verify diacritics such as `č` and `š`, the 8-point IDs, and the 841 mm roll dimension after changing fonts. Older RootsMagic RTF notes use the Linux text converter configured for the app.

## TLS and renewal

Certbot uses the Cloudflare DNS plugin and reads `/etc/rootsmagic-reader/cloudflare.ini` on the VM. Keep the file readable only by the account running Certbot. Let’s Encrypt validates the hostname through DNS. [Certbot Cloudflare plugin documentation](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)

For a fresh issuance, run the command on the VM after installing Certbot and its Cloudflare plugin.

```sh
sudo certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /etc/rootsmagic-reader/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 35 \
  --config-dir /etc/letsencrypt-rootsmagic-reader \
  --work-dir /var/lib/letsencrypt-rootsmagic-reader \
  --logs-dir /var/log/letsencrypt-rootsmagic-reader \
  --cert-name lan-reader.example.org -d lan-reader.example.org -d reader.example.org
```

Check the renewal timer and simulate a renewal for this certificate.

```sh
systemctl status rootsmagic-reader-certificate.timer
sudo certbot renew --cert-name lan-reader.example.org --dry-run \
  --config-dir /etc/letsencrypt-rootsmagic-reader \
  --work-dir /var/lib/letsencrypt-rootsmagic-reader \
  --logs-dir /var/log/letsencrypt-rootsmagic-reader
```

The deploy hook validates the Nginx configuration and reloads it after a successful renewal for this hostname. Check `sudo nginx -t` before enabling or changing the site.

## Verify, update, and roll back

```sh
sudo systemctl status rootsmagic-reader.service
sudo journalctl -u rootsmagic-reader.service -n 100 --no-pager
curl --silent --show-error -o /dev/null -w '%{http_code}\n' https://lan-reader.example.org/api/v1/trees
```

The API should return `401` before sign-in. From a client permitted by your firewall rules, sign in and open the working tree. Verify the starting person and their recorded siblings. Search by a known record ID, switch trees when more than one has been assigned, and test branch expansion, full record images, and **Print PDF**. After publishing a newer snapshot, check that **Refresh** reads it while preserving the current starting person.

Stage application updates as new release directories. Verify the release on the private preview port, point `current` at the tested release, and restart `rootsmagic-reader.service`. For a production rollback, select a release that supports required hosted accounts and two-step verification. The same account state, tree snapshots, and NAS media are shared across releases.

To restore a tree on the VM, log in as `rootsmagic-reader`, select the snapshot from `backups/`, and pass it through the same validation and atomic installation used for publication. Use the original tree filename so saved reader links continue to work.

```sh
root=/srv/rootsmagic-reader
tree='family-tree.rmtree'
ls -lt "$root/backups"
backup="$root/backups/<selected filename>"
checksum=$(sha256sum -- "$backup" | cut -d ' ' -f 1)
python3 "$root/current/deploy/publish-tree.py" --receive "$root" "$tree" "$checksum" < "$backup"
```

Check the restored tree with **Refresh**. The previous active snapshot is backed up during the restore. Keep the snapshots and app releases needed for rollback and remove older ones when their retention is no longer useful.
