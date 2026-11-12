# RootsMagic Reader tasks

Check off a step when its behavior has been verified. **P1** covers the current changes and protections for the hosted reader. **P2** improves family research and publishing. **P3** applies when access expands outside the family.

## P1 · Current changes

- [x] **Open Pedigree by default**
  - [x] Route the home page to the current tree’s pedigree and keep a direct link to the People directory.
  - [x] Verify the default after sign-in, legacy search links, and direct links to a specific tree and person.
- [x] **Add comments to people**
  - [x] Add a **Comments** action to every kind of person card and a section in the details panel for reading and posting.
  - [x] Store comments in an app-owned database by tree and RootsMagic ID. Keep comments through refreshes, restarts, and new tree snapshots.
  - [x] Verify tree permissions on the VM, safe text rendering, and persistence in both the VM and Mac readers.
- [x] **Reorganize pedigree tools**
  - [x] Give research, branch depth, and PDF export their own groups.
  - [x] Verify relationship search, research gaps and worklist, changing depth, and downloading the visible pedigree on desktop and phone.
- [x] **Give My person a direct shortcut**
  - [x] Show it outside **Account** when the signed-in user has linked a person in the current tree.
  - [x] Verify the shortcut and menu entry open the same record and follow the selected tree.

## P1 · Hosted reader administration and protections

- [x] **Add System settings for administrators**
  - [x] Put **System settings** under **Account** for Administrators. Enforce Administrator access on every settings page and action, including direct links.
  - [x] Give each settings section its own page under a shared System settings layout so new sections can be added later.
  - [x] Move **Manage accounts** into System settings and preserve existing account management and invitation links.
  - [x] Add **Email server** for SMTP connection and sender settings. Protect stored credentials and let an Administrator send a test email.
  - [x] Let Administrators test SMTP settings before saving and choose whether the mail server requires authentication.
  - [x] Let Administrators email invitations from **Manage accounts** when mail is configured. Keep the copyable one-time link available.
  - [x] Verify that Administrators can use both sections, Readers are denied direct access, and mail delivery failures leave the invitation link usable.
- [x] **Sign in with a passkey**
  - [x] Let users add, name, and remove passkeys under **Account settings** after confirming their identity.
  - [x] Add **Sign in with a passkey** at the canonical HTTPS origin using WebAuthn with user verification. Keep password sign-in and the existing two-step recovery flow available, including on the LAN address. [WebAuthn standard](https://www.w3.org/TR/webauthn-3/)
  - [x] Verify enrollment, sign-in, removal, and fallback on desktop and phone layouts for both Reader and Administrator accounts, using browser virtual authenticators.
- [x] **Limit abusive requests and keep dependencies current**
  - [x] Update the affected cryptography dependency and run the existing authenticator and recovery-code checks.
  - [x] Apply sign-in and PDF limits and test that one user’s requests leave other family accounts usable.
  - [x] Add security-event logging for sign-ins and account changes. Keep credentials, tokens, and genealogy details out of the logs.
  - [x] Verify the proxy chain passes a trustworthy visitor address. Confirm the public visitor address and verify that forged forwarding headers have no effect on the public and direct routes.
- [x] **Back up comments with the rest of the reader state**
  - [x] Add the comments database to the VM backup routine.
  - [x] Restore the database into an isolated preview and verify comments still match their original tree and person.

## P1.1 · Reader configuration

- [x] **Move reader options into System settings**
  - [x] Add **Trees and chart** to both readers for the default tree, starting person, initial depth, siblings, and children.
  - [x] Add **Local files** to the Mac reader for the working file, discovery folder, and Publish button. Restrict changes to the local loopback reader.
  - [x] Add **Security and limits** on the VM for sign-in and PDF limits, with status for passkeys, audit logs, and proxy trust.
  - [x] Add **Backups** on the VM for retention and the last verified backup. Include saved reader settings in the backup.
  - [x] Verify persistence, permission checks, default chart behavior, and both phone and desktop layouts.

## P2 · Research and publishing

- [x] **Manage comments**
  - [x] Let authors correct their own comments and give administrators a way to remove a comment when needed.
  - [x] Add a recent-comments view for the current tree so family members can find new contributions.
- [x] **Make research gaps actionable**
  - [x] Let the researcher save a gap as a follow-up and mark it complete.
  - [x] Show the saved items by tree, person, and status, with a direct link back to each record.
- [x] **Preview a Publish**
  - [x] Show the selected tree, snapshot changes, and new or missing media before transfer.
  - [x] Summarize what the VM accepted after publication and keep the previous snapshot available for rollback.
- [x] **Share the current chart view**
  - [x] Add a link that preserves the tree, starting person, and visible chart settings.
  - [x] Verify recipients need their existing tree access and see a clear message if the linked record has changed.

## P3 · Sharing outside the family

- [ ] **Add an External Reader role when needed**
  - [ ] Define which living records, private facts, notes, comments, and media external readers may see.
  - [ ] Enforce that policy in chart, search, full records, direct media, and PDF endpoints. Verify access with an external test account.
