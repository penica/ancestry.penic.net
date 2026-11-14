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
  - [x] Add **Sign in with a passkey** at `https://ancestry.penic.org` using WebAuthn with user verification. Keep password sign-in and the existing two-step recovery flow available, including on the LAN address. [WebAuthn standard](https://www.w3.org/TR/webauthn-3/)
  - [x] Verify enrollment, sign-in, removal, and fallback on desktop and phone layouts for both Reader and Administrator accounts, using browser virtual authenticators.
- [x] **Limit abusive requests and keep dependencies current**
  - [x] Update the affected cryptography dependency and run the existing authenticator and recovery-code checks.
  - [x] Apply sign-in and PDF limits and test that one user’s requests leave other family accounts usable.
  - [x] Add security-event logging for sign-ins and account changes. Keep credentials, tokens, and genealogy details out of the logs.
  - [x] Verify the proxy chain passes a trustworthy visitor address. The public route delivered the visitor IP observed by Cloudflare. Forged forwarding headers had no effect through Cloudflare, through the direct proxy route, or on direct VM requests.
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

- [x] **Add an External Reader role when needed**
  - [x] Define which living records, private facts, notes, comments, and media external readers may see.
  - [x] Enforce that policy in chart, search, full records, direct media, and PDF endpoints. Verify access with an external test account.

## Next priorities

The order below reflects day-to-day use of the reader. Publishing safety is the highest priority for changes that affect the published tree.

### 1. Family groups

- [x] **Add a Show family groups toggle to the existing pedigree**
  - [x] Use a shared connector for each recorded parent group to show which children belong together. Place the marriage date and place beside that connector when recorded.
  - [x] Keep groups separate when a parent has children with multiple partners. Show a single recorded parent where the other is unknown, and use the existing blue and plum card borders for recorded sex.
  - [x] Preserve the starting person, open branches, zoom, and chart position when toggling. Check multiple partners and a missing parent on desktop and phone.

### 2. Vertical pedigree view

- [x] **Add an optional Vertical layout to the pedigree**
  - [x] Add **Horizontal** and **Vertical** to the same pedigree page, with **Horizontal** as the default. Build this after **Show family groups**, starting with the desktop layout.
  - [x] Place the starting person below their ancestors. Use the same family-group rules and chart controls in both layouts. When family groups are shown, place parents side by side, show their recorded marriage date and place between them, and group each set of children beneath the corresponding parents.
  - [x] Preserve the starting person, open branches, and **Show family groups** setting when switching layouts. Center the new layout on the starting person. Verify switching in both directions on desktop and phone, including a family with multiple partnerships.

### 3. Resume research

- [x] **Save private named chart views**
  - [x] Let readers save and reopen a starting person with open branches, chart options, zoom, and scroll position. Apply current tree permissions when opening a saved view.
- [x] **Find saved follow-ups by person name**
  - [x] Support name search alongside exact RootsMagic ID. Show the recorded names and IDs so readers can distinguish people with the same name.

### 4. Registry linking

- [x] **Link each named person in a church-book entry**
  - [x] Add name and ID lookup in the registry app through the existing tree-scoped API. Keep the API key on the registry server. Save each link against the relevant person mention in the entry.
  - [x] Store the tree slug, RootsMagic ID, and stable ID when available. Open the full record using the viewer's reader session and flag links for review when a merge changes or reuses an ID.
  - [x] Add automated checks for the registry lookup, multiple people linked to one entry, wrong-tree requests, revoked keys, and record access with the viewer's own permissions.

### 5. Card styling and A0 PDFs

- [x] **Show recorded sex on web chart cards**
  - [x] Use a muted blue left border for men, muted plum for women, and a neutral border when sex is unknown or unrecorded. Use the value recorded in RootsMagic.
  - [x] Place a small **M** or **F** beside the RootsMagic ID so readers can distinguish the cards without relying on color. Keep the existing card backgrounds and starting-person highlight.
  - [x] Test a large pedigree on desktop and phone in light and dark mode. Check the labels at normal zoom and make sure the starting-person highlight and family branch colors are still easy to distinguish.
- [x] **Carry the treatment into the A0 PDFs after reviewing the web chart**
  - [x] Apply the same left borders and **M** or **F** labels to the compact cards. Check a grayscale print at 100% scale to verify the labels and RootsMagic IDs are legible.

### 6. Mobile usability

- [x] **Give the pedigree most of the phone screen**
  - [x] Keep **Search**, **Previous person**, and **Refresh** reachable from a compact toolbar in the normal and full-screen chart views. Group the less-used controls under **Chart tools**. Keep tree switching and the Account menu easy to find.
  - [x] Keep the horizontal pedigree. Let users pan the chart with one finger and reach **Fit to view**, zoom, and **Center** without scrolling away from the chart. Keep card links and **Expand branch** easy to tap separately.
  - [x] Use the available viewport height. At 390 × 844 with menus closed and the keyboard dismissed, aim to give the chart at least half of the initial screen. Check that opening and closing search or tools preserves the current chart position.
- [x] **Make records and account pages comfortable to use on phones**
  - [x] Check that person details, family links, citations, image previews, and the full record remain readable at phone widths. Keep the details panel’s **Close** control visible and return to the same chart and card when it closes.
  - [x] Check search results with the onscreen keyboard open, long names, tree switching, sign-in, Account settings, and administrator forms. Keep controls reachable and avoid horizontal scrolling outside the pedigree canvas.
- [x] **Verify on phones, tablets, and desktop**
  - [x] Verified Chromium and WebKit with phone and tablet emulation at 320 and 390 px, plus tablet and desktop widths. Covered portrait, landscape, light and dark mode and 44 px toolbar targets. Physical iPhone and Android checks remain available as follow-up.
  - [x] Verify search, branch expansion, **Previous person**, changing trees, details, image previews, and refresh with the same starting person, open branches, zoom, and scroll position where applicable. Update the usage and browser checks in the reader guides.

### 7. Publishing safety

- [x] **Validate the staged tree before Publish**
  - [x] Run the reader's search, chart, and record queries against the staged snapshot. Reject an incompatible snapshot before replacing the active tree.
  - [x] Compare stable RootsMagic identities with the published tree. Flag reused IDs and show which comments, follow-ups, shared charts, and registry links could be affected. Keep a link that needs review from silently opening a different person's record.
- [x] **Restore a publication and its NAS media together**
  - [x] Record the tree snapshot, changed media versions, and checksums in the publication history. Restore the matching media and snapshot together, with a preview of what will change.
  - [x] Add automated checks with disposable trees and media for incompatible schemas, reused IDs, stale previews, interrupted publication, and rollback.
