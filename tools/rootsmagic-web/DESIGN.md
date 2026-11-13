# RootsMagic browser

Extend the visual language of a family archive: warm paper, forest green, serif display headings and quiet rules. Dark mode uses forest-charcoal paper. No external assets.

## 4. Layout

Layout: directory and profile - keep names and record prose legible -> center (https://raw.githubusercontent.com/changeroa/StyleGallery/main/patterns/centering/center.md); scroll owner: document; anti-patterns: fixed reading widths and nested scroll containers.

Layout: reader masthead - keep the brand and tree context together, with the current view's navigation and the Account disclosure alongside. Let the tree picker wrap onto its own row when space is tight. The masthead stays above the pedigree scroll area.

Widths: 64rem shell, 46rem reading measure; spacing 4, 8, 12, 16, 24, 32, 48px. System body and Georgia display, 14/16/22/36/48px, with 15/17px text on phones. Fine neutral rules and 4px corners. Light canvas #f6f5f0, surface #fffefb, ink #272f2b, muted #56635a, rule #d8ddd4, control #79877b, accent #294f40 and wash #e7eee8. Dark canvas #1a211d, surface #222b25, ink #eef2ec, muted #bbc8bd, rule #3c4940, control #819385, accent #b3d5bf and wash #2d3c32.

## 5. Screens and primitives

- Directory: search first, then result names and RootsMagic IDs. Primary action: Search. Empty state offers a clear search. Pagination preserves the query.
- Person record: name and RootsMagic ID, family links, then events with citations and notes. Primary navigation is opening a relative. Source details use native disclosure with a keyboard-visible focus ring. The directory link is always available.
- The pedigree full record renders image previews in its initial HTML. Keep fact and citation images with their events. Put remaining person, name, or family images at the end and leave out media already shown with a fact. Use tree-scoped media IDs for same-origin images. Identify registered images that are unavailable locally without loading an image for them.
- The full record and the details drawer share a native image preview dialog. Opening an image keeps the current page and chart in place. Closing the preview returns focus to its link, including when the details drawer is still open.
- Shared: 44px interactive targets, focus ring, link underline on hover, row wash on hover and press. No animation, dashboards, stock portraits or remote fonts.
- Masthead: show the link between the directory and pedigree directly. Group **My person**, **Account settings**, **Manage accounts** for administrators, and **Sign out** in **Account**. Keep **API schema** accessible from the directory. Verify tree switching and the Account disclosure at phone width, including keyboard focus and sign-out.

## Pedigree

Layout: pedigree - fill the available screen below wrapping navigation -> scroll-body-shell (https://raw.githubusercontent.com/changeroa/StyleGallery/main/patterns/viewport-shell/scroll-body-shell.md); scroll owner: `.viewport`; anti-patterns: fixed header-height offsets and a capped canvas width. On very short screens the document can scroll to preserve a usable chart. Keep 16–32px side gutters and a 24px bottom gutter.

- Search by name or RootsMagic ID and select any person card to change the starting person. Place siblings in the starting column and use the starting person's parent links for the chart. Show the current name, generation headings, vital facts, marriage facts, and a reset link.
- Calculate age at death in the read-only data layer and pass the same formatted value to cards, details, Family rows, and PDF export. Measure the age with the rest of the card text before placing PDF cards.
- When multiple files are available, use the same **Tree** selector in the chart and directory. Scope routes by the configured tree slug, including direct person links, evidence, and PDF requests. Person IDs belong to their tree.
- Search results show IDs and available birth and death years. **Show more** appends the next page for the same query. A new search clears the results and cancels any pending search. Keep the result list keyboard-accessible.
- **Go to generation** lists columns present in the chart and scrolls within the chart. **Center on starting person** restores the starting view for the current person. These controls leave the URL and browser history alone.
- **Show children** starts off and stays selected across navigation and refresh within the page. Group the children in the left column by their recorded parental family. Name the recorded parents above each group and connect the group to the starting person and any visible partner card. Preserve the groups in print. Include the children column in chart navigation and exclude it from the ancestor generation count.
- **Show siblings** starts on. Hiding siblings removes their chart cards and PDF entries. Store the toggle in the chart's history entry.
- The chart view controls zoom about the viewport center or fit all drawn cards to the available space. **Full screen** expands the chart to the browser viewport and preserves chart navigation. Escape exits when no dialog is open. The PDF uses the same fixed print scale at every browser zoom level.
- **Initial depth** controls the base ancestry snapshot. An **Expand branch** control is a sibling of the person's card link, so opening a branch and changing the starting person are separate actions. Attach additions to the card's ancestry path so two occurrences of the same person can expand independently. Preserve expansion state during refresh.
- **Chart tools** keeps Research, Branches, and Print together in a collapsible row. The groups sit side by side when space allows and stack at phone width.
- Every person card has a **Comments** button next to **Details**. It opens that person’s details drawer at the Comments section. Keep the chart in place and restore focus to the same card action on close. Comments are plain text, scoped to a tree and RootsMagic ID, and stored independently of RootsMagic notes and tree snapshots.
- An expanded starting person shows recorded partners above the sibling cards in the starting column, even when all of the starting person's ancestors are visible. Use a continuous dashed bracket with square corners and place each couple's marriage facts beside it, above the arm to that partner. Leave room for the label to wrap clear of the child links. Draw each partner's recorded parents in the shared generation columns. Give each partner independent ancestry expansion and collapse, including further expansion on their ancestor cards. Preserve both the starting person's and partners' branch choices on refresh. Print the visible partner cards, marriage facts, and ancestry.
- Relationship search uses a separate native dialog and the same name and ID lookup as the chart. Display the direction of each recorded link and label a bounded search when it reaches its limit. Research gaps are optional and remain textual in both themes.
- The research worklist uses the same gap data as the card flags. Filter the current chart by generation and gap category. Select a result to close the dialog and focus that exact card occurrence in the chart.
- Each missing fact and uncited event can be saved as a follow-up. Saved items belong to the current reader and show their status in the worklist. The follow-up list filters by accessible tree, record ID, and status, and links to the full person record.
- Recent comments lists the selected tree's newest contributions. Comment edits use stable account IDs. Administrators can remove a comment while its ID stays reserved for existing pagination cursors.
- Shared links store the chart settings and a fingerprint of the starting record. Opening a link uses the recipient's current tree permissions and reports changes to the starting record. The reader stores shared links, follow-ups, and comments in the private reader database.
- Publish previews compare record fingerprints and check the linked media before sending the snapshot and media files. Confirmation rechecks the selected snapshot and media, and the VM reports the previous snapshot filename when a tree is replaced.
- Use the existing paper, surface, control, and forest accent tokens. Use the wash for the current card, thin rules for connectors, and subdued green, blue, ochre, and plum for branches. Branch connector tokens, light then dark: #467864 and #80ab96, #56718e and #8faac7, #99703d and #caa676, #795876 and #ba97b7.
- Use 12px for annotations and IDs, 14px for vital details, and 16px for card names. The chart uses 216px cards, 112px connector gutters, and 16px gaps between cards in a generation.

## Account settings

Layout: account settings - centered reading width with one row per setting, each row opening a focused form. Use the account header, existing paper and forest colors, and a visible keyboard focus ring. Keep each row tappable at phone width and allow long usernames and email addresses to wrap. **My person** searches within the chosen tree and identifies each match by RootsMagic ID and available years.

## Person details panel

Layout: selected-person details - preserve chart position beneath a native modal drawer -> scroll-body-shell (https://raw.githubusercontent.com/changeroa/StyleGallery/main/patterns/viewport-shell/scroll-body-shell.md); scroll owner: `.details-scroll`; fixed region: dialog header; height: 100dvh. Panel width is 36rem on desktop and full screen below 43rem.

- Primary action: open a relative as the starting person. Priorities: identity and alternate names, recorded events, then family and personal notes. Keep **Identity**, **Timeline**, **Family**, and **Notes** links in the fixed header. Each link scrolls within the panel and moves focus to its section heading.
- Required content includes RootsMagic ID, living status when recorded, all names, chronological events with dates, places and expandable citations, family links, general notes, full-record link, Close and Previous person.
- Use the same family projection and dividers in the details drawer and server-rendered full record. Order father and mother first, then partners by recorded marriage date with their children beneath them by birth date. Show recorded vital dates or years. Use heavier dividers between parents, partners, their child lists, and siblings, with thin dividers between rows within a list.
- Native dialog owns focus trapping and Escape. Closing restores chart position and opener focus. Family navigation uses existing history; Previous remains visible and disabled until a prior person exists. Full record opens in a new tab. Loading, empty and retry states occupy the same panel.
- A **Details** button is available on every card. Its selected person can differ from the chart's starting person. Panel refresh reads that person's record and the current chart. Restore focus to the matching card's button if the chart was redrawn.
- Reuse existing typography, 4px spacing scale and color tokens; 24px title, 16px section headings, 14px detail copy, 44px controls. The backdrop uses ink at 20% opacity. No animation, extra portraits, badges or duplicate vital summaries. Source citations and event notes use native disclosure.
- Timeline evidence lists recorded HTTP(S) source URLs and registered event or citation media. Open files in a new tab, show unavailable files as text, and use the media ID route to keep local paths out of the browser payload.

## History and refresh

- Each browser history entry stores its tree and person, depth, zoom, toggles, scroll position, and branch paths. **Previous person** reloads that view from the selected file. **Reset view** starts at the current tree's default person and participates in the same history. Chart controls and panel section jumps operate within the current entry.
- Refresh uses the same person's URL and preserves history. Keep the last successful chart or panel visible if its request fails. Show progress and the outcome, and keep a retry available. A refresh from the panel requests the chart and details together, reports partial failures, and restores the panel position and open event disclosures when those events still exist.
- Show **Publish** beside **Refresh** for the Mac working tree when publication is enabled. Display publication progress and its result independently of chart navigation and refresh. Submit from the local page with its request token and block repeated clicks while publication runs.
- The pedigree endpoints use the live database described in the [reader guide](README.md). Responses use `Cache-Control: no-store`, and each request opens a read-only SQLite transaction. Run the compatibility checks in that guide after a RootsMagic upgrade.

## External Reader

- Resolve the account's role for the selected tree on every request. Use the read-only SQLite projection in `reader_external.py` for external search, directory, chart, relationships, and both record views. The projection filters living and private people, private names and links, non-vital or private facts, free-text dates, and exact sites. Derive search years from the permitted vital events. The SQLite authorizer also limits reads of the underlying tables.
- Enforce family access at the evidence, direct media, comments, research, and follow-up endpoints. Hide those controls, Notes, and the tree's **My person** shortcut in the external view. If a preferred starting person is hidden, start with the first visible person.
- Rebuild external PDF cards, facts, and family edges from the projection. The submitted outline selects branches to print. Scope shared links to the recipient's tree access and remove hidden branch IDs before rendering. Compare external change notices against the public record revision when available.

## API keys

Layout: API key settings - reuse the system navigation and administrator form and list layout. Put the one-time secret above the form after creation or rotation. Stack the form and key list on phone widths. Each key row expands to show its tree, access, expiry, last use, and management actions.

- Check administrator access and form CSRF for each management action. Keep key hashes, metadata and timestamps in the account database. Audit creation, renaming, rotation and revocation by internal ID. Reveal the secret only in the issue or rotation response.
- Only the registry lookup accepts a bearer key. Resolve the assigned tree from that key and use the External Reader projection for public deceased access. Return the fields needed to distinguish and link people. Full records and browser links still require a session with tree access.
- Verify key creation, rename, rotation, expiry, revocation, rate limits and concurrent stale form submissions. Check both access scopes, attempts to use the key for another tree or route, and an HTTPS request from a client without a reader session.

## Browser checks before shipping

- Test a desktop and a phone width in light and dark mode. Verify the chart is the scrolling region, generation controls fit, and the fixed panel header leaves room to scroll the details.
- Open #406 in both default files and verify the directory, chart, record, and links agree within each tree. Check that **Reset view** stays in that tree.
- Check that **Full screen** grows the chart, Escape exits it, **Fit to view** shows all cards, and zoom keeps the chart centered near the same point. After opening and closing branches and changing the zoom, re-root and use **Previous person** to restore the view.
- On #227 in the working tree, show children and verify both parental families on the chart and in the A0 PDF. Open the research worklist, filter by category and generation, and select an entry. The card receives focus and the starting person remains the same.
- Start with #37 and #240. Test generation jump, Center, search by name and ID, Show more, Previous, and Reset. On the direct link to #614, check the fallback child link and that Show children places #612 to the left. Confirm keyboard focus stays visible.
- Open #406. Check the section jumps, citations, a family link, and Escape returning focus to **View details**. Verify **Open full record** uses the pedigree database.
- Open #227 in Details and the full record. Verify the parents, the dated partnership before the undated partnership, their two respective child lists in birth order, the vital dates, and the thicker group dividers.
- Verify #127 shows an age of 59 years, #549 shows about 2 months using baptism, and #596 shows about 64 years using burial in the chart, details, and PDF. Verify that living records such as #37 have no age at death.
- Open the full record for a person with linked media and verify inline images render with JavaScript disabled. With JavaScript enabled, check the popup on the full record and in the details drawer. Verify the selected tree, focus return, placement alongside facts, and **Other images** at the bottom.
- Expand a starting person with a recorded partner and verify the partner and their parents appear. Verify the dashed partnership connector, collapse and reopen the partner's ancestry, and refresh with each state. Check that the starting person is preserved, selecting the partner's card starts their pedigree, and PDF export follows which branches are visible.
- Expand a starting person with a recorded marriage date and place. Verify those facts label the dashed partner connector on screen and in the PDF. Check that the label remains with the partner’s ancestry collapsed and is legible with **Show children** on.
- Use a temporary copy of the database to check a saved change and a failed refresh. Verify the old data remains readable on failure, then confirm retry updates the chart and open panel while preserving the view. Run the API examples from the reader guide.
- At depth 3, expand both of #37's parent branches, collapse one, and verify the other remains. Check a relationship between #37 and #240, the gap flags for #614, and source and media links in #406's timeline. Download the visible chart and verify the PDF is one page, 841 mm high, with embedded text and the correct people.
- With a disposable tree and account, use **Manage accounts** to invite an External Reader. Verify name and ID search, chart and relationship links, Details, and the full record. Request a hidden person's URL and a known media ID directly. Open a family member's shared link and check the saved branch IDs. Export a PDF using a request with modified card text or hidden relatives and verify the PDF contains only permitted server records. Change the test account from Reader to External Reader in an open session and verify access in both a restricted tree and a tree where it still has Reader access. Repeat these checks after a RootsMagic schema upgrade.
