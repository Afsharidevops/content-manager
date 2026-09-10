# Google Flow region unlock — laptop-only guide

You can keep `flow.google.com` usable on a laptop without installing Media
Studio, Docker, or anything from this stack. The unlock is a plain Chrome
extension plus an optional uBlock filter; both live in this repository under
`extensions/locallab-flow-unlock/` and do not talk to any other service.

What it does:

1. A content script in the page rewrites the two batchexecute answers that
   carry the region verdict before the web app reads them: the country and age
   flags of `VideoFxService.GetFlowAppConfig` (`cPZSdc`) and the tool status of
   `AiSandbox.CheckToolAvailability` (`KV2T2d`). Only those fields change, so
   the dashboard renders for the account in the tab.
2. Browser rules abort every request to the Flow `/unsupported-country` page:
   the bare path, multi-account paths such as `/u/1/unsupported-country`,
   `flow.google-*.com` variants, and the `labs.google` tool path. The redirect
   that replaces the Flow UI therefore never lands.
3. As a fallback for tabs where the patched answer does not arrive, a second
   content script stops in-flight network for a short window as soon
   as the project creation button appears, which prevents the background
   region check from swapping the editor for the block page. It stands down as
   soon as the page carries the `data-locallab-flow-config="patched"` marker.
4. The fallback script also watches the address bar. When the web app swaps the route
   client-side (no network request for the rule set to block), it restarts the
   app root, at most twice per tab, so a client-side route change cannot
   strand the tab on the block page.
5. A background worker returns the tab to the app root (up to twice a minute)
   when a blocked navigation still leaves Chrome's `ERR_BLOCKED_BY_CLIENT`
   page, where no content script can run.

Requirements:

- Chrome or Chromium on the laptop (any OS).
- A Google account that is signed in at https://flow.google.com.
- The folder `extensions/locallab-flow-unlock` from this repository (clone the repo or
  download a release zip and extract it).

## Option A — install the extension (recommended)

1. Keep the repository folder somewhere stable, e.g.
   `~/lab/projects/content-manager/extensions/locallab-flow-unlock`. Do not delete or
   move it after loading; Chrome reads it from disk.
2. Open `chrome://extensions`.
3. Enable **Developer mode** (top-right toggle).
4. Click **Load unpacked** and select the `extensions/locallab-flow-unlock` folder.
5. Confirm the "Locallab Flow Unlock" card appears and its toggle is on.
6. (Optional) Pin it next to the address bar so you can see it is active.

## Option B — uBlock Origin filter (alternative or backup)

1. Install uBlock Origin from the Chrome Web Store.
2. Open the uBlock dashboard (the extension icon → settings gear).
3. Go to the **My filters** tab.
4. Paste these two lines and click **Apply**:

```text
||flow.google.com/unsupported-country^$document
||flow.google.com/*unsupported-country$document
||flow.google-*.com/*unsupported-country$document
||labs.google/*unsupported-country$document
```

The filter file is also stored at
`extensions/locallab-flow-unlock/ublock-filter.txt`.

## Check that the patch applied

The page marks itself once the config answer was rewritten. Open the Flow tab,
open DevTools, and run this in the console:

```js
document.documentElement.getAttribute('data-locallab-flow-config')
```

`"patched"` means the dashboard should render. If it returns `null` on an
account that still shows the block page, the answer arrived in a shape the
script did not patch: reload the extension and send the `cPZSdc` response from
the Network tab (right-click the request, **Copy response**) so the parser can
be extended.

## Use

1. Open https://flow.google.com in the same Chrome.
2. Sign in to the Google account that has Flow access if you are not signed
   in yet. Multi-account URLs such as `/u/1/` are covered, but Flow access is
   granted per account: when one signed-in account reaches the dashboard and
   another only reaches its projects page, that difference is what the
   response patch removes.
3. Create a project as usual. The first screen may take a moment; if the page
   freezes briefly when the "New project" button appears, that is the unlock
   working.

## Troubleshooting

- Still see "not available in your country" or a blank redirect? Hard-refresh
  once (`Ctrl+Shift+R`), close and reopen the Flow tab, then check the
  extension is still enabled at `chrome://extensions`.
- If the block page appears the moment Flow opens, before the project
  dashboard is ever drawn, the region check ran before the freeze could
  trigger. Capture the triggering call: open DevTools on the Flow tab,
  **Network** tab, tick **Preserve log**, reload, reproduce the block page,
  then filter for `country`, `region`, and `unsupported` and inspect the
  failing request. Blocking or stubbing that request is the durable fix.
- Multi-account URLs: after editing `rules.json`, `freeze.js`, or
  `background.js`, press **Reload** on the extension card. Chrome only
  re-reads the extension files when it reloads.
- Never hard-refresh a tab whose address bar still shows the block route: the
  rule set aborts that navigation and Chrome shows "This page has been
  blocked by an extension". Type `https://flow.google.com/` instead, or let
  the background worker bring the tab back.
- Flow decides the verdict in the page, from the `cPZSdc` and `KV2T2d`
  answers, which is why the response patch works; the app shell itself is
  served with `200` for both `/` and `/unsupported-country`. If an account
  still reports an unsupported country after a reload, confirm the marker
  above, then compare the `cPZSdc` response of that account with a working
  one and extend the patch.
- Buttons show raw text such as `add` or `videocam` instead of icons? That is
  only the icon font failing to load and is cosmetic; the labels remain
  clickable.
- "Sign in" appears instead of projects? Flow still requires a Google account
  with an eligible subscription; the unlock cannot replace the sign-in.
- After a Flow UI update the freeze step may stop matching the button; reload
  the extension from `chrome://extensions` after pulling the latest repo
  files.

This standalone path is independent of Media Studio; when you later run Media
Studio on a server, the bundled extension applies the same response patch and
the `flow-video` driver adds request interception
(`MEDIA_STUDIO_BLOCK_GEO_REDIRECT`) and the page freeze
(`MEDIA_STUDIO_FREEZE_ON_READY`). See `docs/MEDIA-STUDIO.md`.
