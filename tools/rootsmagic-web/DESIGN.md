# RootsMagic browser

Extend the visual language of the Penič family archive: warm paper, forest green, serif display headings and quiet rules. Dark mode uses forest-charcoal paper. No external assets.

## 4. Layout

Layout: directory and profile - keep names and record prose legible -> center (https://raw.githubusercontent.com/changeroa/StyleGallery/main/patterns/centering/center.md); scroll owner: document; anti-patterns: fixed reading widths and nested scroll containers.

Layout: On this day - use the directory reading width, with a wrapping date form above stacked event groups. The document owns scrolling.

Layout: reader masthead - keep the brand and tree context together, with the current view's navigation and the Account disclosure alongside. Let the tree picker wrap onto its own row when space is tight. The masthead stays above the pedigree scroll area.

Widths: 64rem shell, 46rem reading measure; spacing 4, 8, 12, 16, 24, 32, 48px. System body and Georgia display, 14/16/22/36/48px, with 15/17px text on phones. Fine neutral rules and 4px corners. Light canvas #f6f5f0, surface #fffefb, ink #272f2b, muted #56635a, rule #d8ddd4, control #79877b, accent #294f40 and wash #e7eee8. Dark canvas #1a211d, surface #222b25, ink #eef2ec, muted #bbc8bd, rule #3c4940, control #819385, accent #b3d5bf and wash #2d3c32.

## 5. Screens and primitives

- Directory: search first, then result names and RootsMagic IDs. Primary action: Search. Empty state offers a clear search. Pagination preserves the query.
- On this day: enter Day, Month, and Year in that order, with the month shown by name. **Calendar** opens a Monday-first month view and fills the date fields. Arrow keys move across days and weeks. Escape closes the calendar and returns focus to its button. The date form also works without JavaScript. Each result opens the person's pedigree in the selected tree.
- Daily report: use the Account settings form for a reader's opt-in and tree choice. Use System settings for the administrator's delivery time, time zone, and pause switch. Verify the address and recheck tree grants before sending the same anniversary sections by email.
- Person record: name and RootsMagic ID, family links, then events with citations and notes. Primary navigation is opening a relative. Source details use native disclosure with a keyboard-visible focus ring. The directory link is always available.
- The pedigree full record renders image previews in its initial HTML. Keep fact and citation images with their events. Put remaining person, name, or family images at the end and leave out media already shown with a fact. Use tree-scoped media IDs for same-origin images. Identify registered images that are unavailable locally without loading an image for them.
- The full record and the details drawer share a native image preview dialog. Opening an image keeps the current page and chart in place. Closing the preview returns focus to its link, including when the details drawer is still open.
- Shared: 44px interactive targets, focus ring, link underline on hover, row wash on hover and press. No animation, dashboards, stock portraits or remote fonts.
- Masthead: show the link between the directory and pedigree directly. Group **My person**, **Account settings**, **Manage accounts** for administrators, and **Sign out** in **Account**. Keep **API schema** accessible from the directory. Verify tree switching and the Account disclosure at phone width, including keyboard focus and sign-out.

## Pedigree

Layout: pedigree - fill the available screen below wrapping navigation -> scroll-body-shell (https://raw.githubusercontent.com/changeroa/StyleGallery/main/patterns/viewport-shell/scroll-body-shell.md); scroll owner: `.viewport`; anti-patterns: fixed header-height offsets and a capped canvas width. On very short screens the document can scroll to preserve a usable chart. Keep 16–32px side gutters and a 24px bottom gutter.

Layout: phone family - full-width rows in a vertical reading pane below search, the current person's name, and the view switch. The pane scrolls independently and puts parents first, followed by siblings, partners, and children grouped by recorded family. Tapping a name starts from that person, and Details opens the same panel as the chart. Chart controls apply to the chart view. Open a saved or shared chart in Chart view.

Layout: standalone pedigree - the chart scrolls beneath a wrapping toolbar. Use compact PDF-style cards and the Reader's branch and sex colors. Selecting a card opens a scrollable facts dialog and closing it returns focus to the card. The file opens on the starting person with three generations visible and expands deeper ancestry from the bundled data.

- Search by name or RootsMagic ID and select any person card to change the starting person. Place siblings in the starting column and use the starting person's parent links for the chart. Show the current name, generation headings, vital facts, marriage facts, and a reset link.
- Calculate age at death in the read-only data layer and pass the same formatted value to cards, details, Family rows, and PDF export. Measure the age with the rest of the card text before placing PDF cards.
- When multiple files are available, use the same **Tree** selector in the chart and directory. Scope routes by the configured tree slug, including direct person links, evidence, and PDF requests. Person IDs belong to their tree.
- Search results show IDs and available birth and death years. **Show more** appends the next page for the same query. A new search clears the results and cancels any pending search. Keep the result list keyboard-accessible.
- **Go to generation** lists columns present in the chart and scrolls within the chart. **Center on starting person** restores the starting view for the current person. These controls leave the URL and browser history alone.
- **Children** starts off and stays selected across navigation and refresh within the page. Horizontal and print show immediate children by recorded parental family. Vertical has an independent descendant depth, stored in chart history and saved and shared views. Allocate horizontal space by subtree width so the child rows stay aligned with their families. Descendant rows appear in generation navigation and the research worklist.
- **Show siblings** starts on. Hiding siblings removes their chart cards and PDF entries. Store the toggle in the chart's history entry.
- **Show family groups** starts off and is saved in history and shared views. Use a shared connector for each visible parent group, and associate marriage facts with the RootsMagic family ID. Keep child groups separate for different partnerships and draw the available parent card when the other is unknown. In Horizontal, the bracket stays between its own parent cards and a curved line reaches it from the child. Keep each marriage date and place beside its dotted partner line even when the child group also displays its marriage fact.
- The chart view controls zoom about the viewport center or fit all drawn cards to the available space. **Full screen** expands the chart to the browser viewport and preserves chart navigation. Escape exits when no dialog is open. The PDF uses the same fixed print scale at every browser zoom level.
- **Initial depth** controls the base ancestry snapshot. Ancestor and partner cards can hide their own ancestor subtree, including ancestors in the initial snapshot. A branch at the visible limit can load recorded parents. Store expansion and collapse by the card's ancestry path, so repeated people can be controlled independently. Restore collapsed paths on refresh, browser navigation and saved or shared views. Continue accepting older views that only stored collapsed partners.
- The Layout selector sits beside Chart tools and stays reachable when the disclosure is closed. Horizontal and Vertical use the same graph and card state. Vertical puts the oldest generation at the top, places partners on alternating sides of the starting person, and routes each set of children below its shared partnership connector. Repeated marriages keep their own family labels and child connectors. The layout is stored in history and shared and saved views. Older views default to Horizontal with family groups off. Switching layout centers the current starting person.
- Vertical reserves horizontal space for each ancestry branch. A pair of parents sits centered above their child, with a single stem from the child to both parents. Family groups adds a shared line for full siblings below the parent connectors.
- **Chart tools** keeps research, branch depth, generation navigation and print in a disclosure that opens over the chart. The family groups, children and siblings switches sit alongside Layout and Chart tools, with a dedicated row when space is tight. The toolbar stays above the scrolling chart. Search, Previous, Refresh, Center, Fit, and zoom stay outside the disclosure. Search results scroll independently.
- Named saved charts live in the existing private Reader state database. Reads and changes use the authenticated owner and current tree access. A stable RootsMagic identity guards the starting number. Opening a saved chart revalidates the branch paths in the current tree projection before drawing.
- Every person card has a **Comments** button next to **Details**. It opens that person’s details drawer at the Comments section. Keep the chart in place and restore focus to the same card action on close. Comments are plain text, scoped to a tree and RootsMagic ID, and stored independently of RootsMagic notes and tree snapshots.
- Expanding the starting person brings all recorded partners and their immediate ancestors into the chart. Before expansion, **Children** displays recorded co-parents. Explicitly collapsing the starting person's branch hides the partner cards in both family-group modes. In Horizontal, use a continuous square dashed bracket with marriage facts beside it. Print the visible partner cards, marriage facts, and ancestry.
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

- Edit email and verification use the same form width. Keep the verification status within its panel and ask for the password when the verification action is opened. Daily reports and account actions use labeled links in HTML email and full URLs in the plain-text alternative.

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
- Refresh uses the same person's URL and preserves history. Success messages clear after five seconds. Keep failure messages and the last successful chart or panel visible so the user can retry. A refresh from the panel requests the chart and details together, reports partial failures, and restores the panel position and open event disclosures when those events still exist.
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
- Open **On this day** from the masthead and from **Tools** at phone width. Check the date picker, Today, tree switching, pedigree links, empty groups and the External Reader view.
- Open #406 in both default files and verify the directory, chart, record, and links agree within each tree. Check that **Reset view** stays in that tree.
- Check that **Full screen** grows the chart, **Refresh** reloads it in place, Escape exits full screen, **Fit to view** shows all cards, and zoom keeps the chart centered near the same point. On wide screens, full-screen **Refresh** sits beside **View details**. After opening and closing branches and changing the zoom, re-root and use **Previous person** to restore the view.
- Open Anton Mihelčič (#290) in Horizontal with **Family groups** on. Check that Michael Mihelčič (#742) and Maria Straschar (#743) have separate brackets connecting them to their own parents, and that the marriage labels are still readable.
- On #227 in the working tree, show children and verify both parental families on the chart and in the A0 PDF. Open the research worklist, filter by category and generation, and select an entry. The card receives focus and the starting person remains the same.
- Start with #37 and #240. Test generation jump, Center, search by name and ID, Show more, Previous, and Reset. On the direct link to #614, check the fallback child link and that Show children places #612 to the left. Confirm keyboard focus stays visible.
- Open #406. Check the section jumps, citations, a family link, and Escape returning focus to **View details**. Verify **Open full record** uses the pedigree database.
- Open #227 in Details and the full record. Verify the parents, the dated partnership before the undated partnership, their two respective child lists in birth order, the vital dates, and the thicker group dividers.
- Verify #127 shows an age of 59 years, #549 shows about 2 months using baptism, and #596 shows about 64 years using burial in the chart, details, and PDF. Verify that living records such as #37 have no age at death.
- Open the full record for Johannes Nepomuk Penič (#406) and verify inline images render with JavaScript disabled. With JavaScript enabled, check the popup on the full record and in the details drawer. Verify the selected tree, focus return, placement alongside facts, and **Other images** at the bottom.
- Expand Johannes Nepomuk Penič (#406) and verify Anna Kopitar (#397) appears with Peter Kopitar (#388) and Maria Pletnar (#393) in the Parents column. Verify the dashed partnership connector, collapse and reopen Anna's ancestry, and refresh with each state. Check that Johannes remains the starting person through these controls, selecting Anna's card starts her pedigree, and PDF export follows which branches are visible.
- Expand Anna Požar (#589) and verify the marriage of 27 November 1775 in Pišece labels the dashed line to Joannes Merslautsch (#588) on screen and in the PDF. Check that the label remains with his ancestry collapsed and is legible with **Show children** on.
- Use a temporary copy of the database to check a saved change and a failed refresh. Verify the old data remains readable on failure, then confirm retry updates the chart and open panel while preserving the view. Run the API examples from the reader guide.
- At depth 3, expand both of #37's parent branches, collapse one, and verify the other remains. Check a relationship between #37 and #240, the gap flags for #614, and source and media links in #406's timeline. Download the visible chart and verify the PDF is one page, 841 mm high, with embedded text and the correct people.
- With a disposable tree and account, use **Manage accounts** to invite an External Reader. Verify name and ID search, chart and relationship links, Details, and the full record. Request a hidden person's URL and a known media ID directly. Open a family member's shared link and check the saved branch IDs. Export a PDF using a request with modified card text or hidden relatives and verify the PDF contains only permitted server records. Change the test account from Reader to External Reader in an open session and verify access in both a restricted tree and a tree where it still has Reader access. Repeat these checks after a RootsMagic schema upgrade.


## Verification for charts and publications

- At 320 and 393 px, short landscape, and desktop, check the available views in light and dark in both WebKit and Chromium. On phones, verify the Family pane opens first, names and facts wrap without horizontal scrolling, and relatives below the fold remain reachable. Switch to Chart in both directions and verify the person, saved chart settings, and scroll positions. Verify touch panning, search, Escape, details focus restoration, branch controls, full screen, and refresh.
- Test repeated partnerships, a parent who is unknown, and restricted-reader access. Reopen a private chart in its owner’s session, verify another session cannot read or change it, and repeat after removing tree access. Use a name with accents and enough follow-ups for more than one page.
- Run `python3 -m unittest discover -s tools/rootsmagic-web/tests` from the workspace. Verify the deployed validator against a staged copy and a deliberately incompatible schema. Interrupt an isolated publish after a media write, recover it twice, then restore the previous snapshot and media using the matching preview.
