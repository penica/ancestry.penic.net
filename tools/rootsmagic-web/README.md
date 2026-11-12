# RootsMagic Reader

Browse the family tree on this Mac and query it through a local JSON API.

For the dedicated Linux reader, see the [VM deployment and update guide](deploy/README.md).

The VM reader uses invitation-based accounts. **Reader** gives access to assigned trees. **Administrator** gives access to all trees and **Account → System settings → Manage accounts** for invitations and access changes.

Open **Account → Account settings** on the VM to edit your name, username, contact email, and password. Choose **My person** to find your record by name or RootsMagic ID in an assigned tree. Once saved, the **My person** button in the header and the **Account → My person** entry both open that record. The choice is saved per tree.

Under **Account settings → Two-step verification**, enter your current password and scan the QR code with an authenticator app. Enter the six-digit code to finish setup, then save the one-time recovery codes. At sign-in, enter an authenticator code or one recovery code after your password.

On your hosted reader’s canonical HTTPS origin, choose **Account settings → Passkeys** to register a device, password manager, or security key. Confirm your password and, when enabled, enter an authenticator or recovery code. Then use **Sign in with a passkey**. The LAN address supports password sign-in and the existing two-step verification.

Administrators can configure SMTP in **Account → System settings → Email server**. Set **SMTP authentication** to **Disabled** for a relay that accepts mail without a login. Use **Send test email** to check the settings before saving. Once configured, **Manage accounts** accepts an optional recipient when creating an invitation and displays the one-time link for copying.

Use **System settings → Trees and chart** to choose the default tree, starting person, ancestry depth, and which relatives appear initially. Saved defaults apply to new chart views. On the Mac, **System settings → Local files** sets the preferred `.rmtree` file, the folder to scan, and whether to show **Publish** for the configured publishing file. The Mac and VM save their settings separately from the tree.

## Start and stop the reader

Run the following from the repository root on your Mac. On the first run, uv installs the script's Python dependencies.

```sh
ROOTSMAGIC_DB="/absolute/path/to/your-tree.rmtree" uv run tools/rootsmagic-web/serve.py
```

Open the [live pedigree](http://127.0.0.1:8765/) or the [people directory](http://127.0.0.1:8765/people). Press **Ctrl+C** in the terminal running the reader to stop it.

## Use the pedigree

- Open **Chart tools** and choose from **Research**, **Branches**, and **Print** to find relationship and research controls, initial depth, and PDF export.
- Use **Tree** and **Open tree** when more than one `.rmtree` file is available. The directory, pedigree, records, search, media, and print use the selected file. A pedigree link includes both the tree and the RootsMagic ID.
- The chart opens at the configured starting person, or the first recorded person when no default is saved. Recorded siblings appear when enabled. Select any card to start from that person. A direct link to someone with no recorded parents shows their recorded children when there is no previous person in your browsing history.
- Search by name, including alternate spellings, or enter an ID such as `#123`. Use the RootsMagic ID and any recorded birth and death years to distinguish matching names. **Show more** loads the next page.
- **Go to generation** scrolls to a generation in the current chart. **Center on starting person** brings the current starting person back into view. **Previous person** restores the last chart, including its open branches, initial depth, toggles, zoom, and scroll position. **Reset view** returns to the current tree's starting person.
- Use **−** and **+** to zoom, or **Fit to view** to see the whole chart at once. **Full screen** gives the chart the available browser space. Use **Exit full screen** or Escape to leave it.
- Turn on **Show children** to add the starting person's children to the left of the chart, grouped by their recorded parental family. The headings show both recorded parents and indicate when the other parent is missing. Select a child to start their pedigree. If the starting person has no recorded children, the chart shows a message.
- Clear **Show siblings** to hide the starting person's brothers and sisters from the chart and PDF. The toggle is on by default and its state is kept when you refresh or return with **Previous person**.
- Choose an **Initial depth** to show fewer generations before opening individual branches. **Expand branch** adds up to two generations beyond the current end of that person's ancestry. **Collapse branch** hides those additions. Changing the starting person or initial depth starts a fresh set of branches.
- On the starting person, **Expand branch** also shows recorded spouses and partners below their card. The couple's recorded marriage date and place appear beside the dashed connector. Each partner's parents appear to the right in the shared generation columns. Use the branch button below a partner card to collapse or reopen their ancestry independently. Expand a parent card to follow its ancestry further when more records are available.
- Use **Find relationship** to search for any starting person by name or ID and see recorded paths to the person above the chart. Each name in a path opens that person's pedigree. The reader shows multiple ancestry paths where recorded, or shortest connections through recorded family links, and reports when it reaches the search limit.
- Turn on **Show research gaps** to flag missing parent links, missing vital dates and places, and events without linked citations on each visible card. Baptism and burial can supply fallback display facts, and an uncited event is an indicator to check the record's evidence.
- Cards, details, and PDFs show the calculated age at death when dates allow it. Birth and death dates take priority, with baptism or christening and burial as the respective fallbacks. Dates explicitly marked **About** come after those choices. Partial dates and estimates based on baptism, burial, or approximate dates are shown as approximate ages.
- Open **Research worklist** to list those gaps in the displayed cards. Filter by category or generation, then select a result to focus its card. Open more branches to include those generations in the worklist.
- **View details** opens the starting person's identity, timeline, family, and notes. Use **Details** on any card to inspect that person while keeping the current chart. Use the section links to jump within the panel. Expand the sources under an event to read its citations, select a relative to open their pedigree, or choose **Open full record** to open the profile in a new tab. **Close** or Escape returns focus to the button you used.
- Use **Comments** on any card to read and add comments for that person in the current tree. The reader stores comments in its own database. The Mac and VM each have their own comments, and publishing a tree snapshot leaves the VM comments in place.
- In **Family**, parents appear first, followed by each partner and their recorded children. Rows show recorded birth and death dates or years, and partner rows show the marriage date when known. Partnerships are ordered by marriage date and children by birth date. Undated entries follow dated entries within their group. Siblings appear after the partner and child groups.
- Under a timeline event, **Source links** opens recorded web references. Select an image under **Linked media** to preview it in a popup. Other files open from their links. Mount the relevant volume if a registered local file is unavailable.
- **Open full record** renders linked images alongside their facts. Images linked only to the person, names, or family appear under **Other images** at the bottom. Select an image to open its popup and use **Close** or Escape to return to the same spot. A registered image that is unavailable locally is identified by its caption or filename.
- **Print PDF** downloads the current chart in the original compact style for an **841 mm A0 roll**, including visible partner ancestry, other expanded branches, and children. Print at **100%** to retain the 8-point Arial IDs. PDF export uses the macOS Arial, Arial Bold, and Georgia fonts. If the chart cannot fit at that type size, collapse a branch or reduce the initial depth and retry.
- Save in RootsMagic, then click **Publish** in the Mac reader to send a verified working-tree snapshot to the VM. The publisher copies new linked files to the NAS and checks media access before publishing the snapshot. Click **Refresh** on the VM reader after publication succeeds. [Publishing and media guide](deploy/README.md#publish-a-tree-update)
- Save your changes in RootsMagic first, then click **Refresh**. The chart keeps your open and collapsed branches. Refreshing in the details panel reloads the chart and the panel. If a refresh fails, use **Refresh** or **Try again**. The last successfully loaded data stays available while you retry.

## Database and troubleshooting

- Before you save reader defaults, the reader lists `family-tree.rmtree` and `legacy-tree.rmtree` at the repository root, if present. Links without a tree use the configured default when it is available to the signed-in account.
- Set `ROOTSMAGIC_DB` to an absolute path before starting the server to use a particular compatible file. Set `ROOTSMAGIC_TREE_DIR` to an absolute directory to include additional `.rmtree` files immediately inside it. Only point it at a folder whose trees you intend to make available in the reader.

To discover trees in a dedicated folder, run this from the repository root. Reload the browser tab after adding a file to update the **Tree** dropdown.

```sh
ROOTSMAGIC_TREE_DIR="/absolute/path/to/trees" uv run tools/rootsmagic-web/serve.py
```

To start with a specific file, set `ROOTSMAGIC_DB`.

```sh
ROOTSMAGIC_DB="/absolute/path/to/your-tree.rmtree" uv run tools/rootsmagic-web/serve.py
```

| Symptom | What to check |
| --- | --- |
| Database unavailable | Check that the file exists, is readable, and uses a supported RootsMagic schema. Restart the reader after changing `ROOTSMAGIC_DB`. |
| Port 8765 already in use | Open the pedigree URL to see whether the reader is already running. To restart it, stop the existing reader from its terminal before starting it again. |
| A saved change is missing | Confirm the file in RootsMagic using **File → Current File**, verify it matches the reader's configured file, and click **Refresh** after the save finishes. Check the full record if you edited an event the chart does not summarize. |

After a RootsMagic file conversion, start the reader with `ROOTSMAGIC_DB` pointing to a copy of the converted file. Try a known search, a pedigree with parent and sibling links, and a full record with event dates and citations before switching the reader to that file.

## Local API

| Endpoint | Result |
| --- | --- |
| `GET /api/v1/trees` | Available tree names and URL slugs, including the default. |
| `GET /api/v1/pedigree/people?q=Example+Name&limit=20&offset=0` | Pedigree search by name or ID. Results include birth and death years when recorded in the selected name record. |
| `GET /api/v1/pedigree/100?generations=11` | Starting person, ancestors, siblings, partners, partner marriage facts, children, and recorded child families. The generation count accepts 1–11. |
| `GET /api/v1/pedigree/123/details` | Recorded names, family, events, citations, and notes from the pedigree database. |
| `GET /api/v1/pedigree/123/comments?limit=50` | Reader comments for that person in the selected tree. Use the returned `next_before` as `before` to read older comments. |
| `POST /api/v1/pedigree/123/comments` | Add a plain-text comment with JSON `{"body":"…"}` and the current page’s `X-CSRF-Token`. The hosted reader uses the signed-in account as author. |
| `GET /api/v1/pedigree/123/relationship?from_id=100` | Recorded connections from the chosen person to the selected person. |
| `POST /api/v1/pedigree/research-gaps` | Missing fields and uncited events for a JSON body containing `person_ids`. Accepts up to 100 IDs per request. |
| `GET /api/v1/pedigree/123/evidence` | Recorded source URLs and linked media by event, including availability. |
| `GET /api/v1/pedigree/media/273` | Registered local media file when it is available. |
| `POST /api/v1/pedigree/print` | A0 roll PDF for the visible `chart` with optional `show_children`, `show_partners`, and `partner_roots`. The `partner_roots` list contains the pedigree `root` nodes for visible partner ancestry. |
| `GET /pedigree/people/123/record` | Full browser record from the pedigree database. |
| `GET /api/v1/people?q=Example&limit=20&offset=0` | Case and accent insensitive name search, including alternate names; an exact RootsMagic ID also works. Empty `q` lists the tree. |
| `GET /api/v1/people/{person_id}` | Person, note, immediate relatives, personal and couple events, and citations linked directly to those events. |
| `GET /openapi.json` | Machine readable API contract. |

Pass `tree=<slug>` to select a different file for any of the tree-specific endpoints. Run these examples on this Mac while the reader is running. Substitute IDs and a tree slug from your own file.

```sh
curl -fsSG http://127.0.0.1:8765/api/v1/pedigree/people --data-urlencode 'q=Example Name'
curl -fsS http://127.0.0.1:8765/api/v1/trees
curl -fsS 'http://127.0.0.1:8765/api/v1/people/123?tree=legacy-tree'
curl -fsS 'http://127.0.0.1:8765/api/v1/pedigree/100?generations=11'
curl -fsS http://127.0.0.1:8765/api/v1/pedigree/123/details
curl -fsS http://127.0.0.1:8765/api/v1/pedigree/123/comments
curl -fsSG http://127.0.0.1:8765/api/v1/people --data-urlencode 'q=Example'
curl -fsS http://127.0.0.1:8765/api/v1/people/123
```

The app opens SQLite with `mode=ro` and `query_only`. Each request reads the current file in a single transaction. Keep editing in RootsMagic. The app preserves original event date encodings in `date_raw` and shows uncommon encodings as recorded.

The API and browser bind to localhost only. They can include living people, private notes, and linked media. Keep the reader local to this Mac. Name-level citations and separate archival research notes remain outside the pedigree.
