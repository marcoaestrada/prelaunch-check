# prelaunch-check

A small, read-only command-line tool that checks a website **before you open it to the public**. It answers the questions that are easy to forget on launch day:

- Is the site really closed to search engines (or really open, if that is the plan)?
- Do the private pages send an anonymous visitor to a login, **on the main address and on every preview address**?
- Do other addresses (`www`, plain `http://`) end up on the right one?
- Is anything from an old name, a placeholder or a test still on the pages?
- Are the basics in place: title, description, one `h1`, viewport, security headers, no HTTP resources on an HTTPS page, no broken internal links?

Python 3.9+, **standard library only** (no dependencies), MIT license. It sends `GET` requests only and never logs in, submits forms or sends anything but a plain page request.

> It grew out of a real launch where the private pages were protected on the main domain but still open on the hosting platform's per-deployment preview addresses. A check that tests those addresses too would have caught it on day one.

## Quick start

```bash
git clone https://github.com/marcoaestrada/prelaunch-check.git
cd prelaunch-check
cp example.prelaunch.json prelaunch.json     # edit it for your site
python -m prelaunch_check prelaunch.json
```

Example output (shortened):

```
prelaunch-check 0.1.0 · https://example.com · closed to search engines

  PASS  reachability    /  -> 200
  PASS  indexing        /  -> noindex present
  PASS  protected       example.com/clients/  -> redirects away to a login (302)
  FAIL  protected       abc123.my-project.pages.dev/clients/  -> EXPOSED: anonymous visitors get 200 with content
  WARN  headers         strict-transport-security  -> missing: HSTS tells browsers to always use HTTPS

32 passed, 1 failed, 1 warnings, 1 notes.
```

Exit codes: `0` nothing failed · `1` at least one `FAIL` (or a `WARN` with `--fail-on-warn`) · `2` configuration error. Add `--json` for machine-readable output, for example in a CI job.

## Configuration

A JSON file. Only `site` is required.

| Key | Meaning | Default |
|---|---|---|
| `site` | The origin to check, e.g. `https://example.com` (no path) | required |
| `expect_indexable` | `false` = the site must stay closed to search engines (`noindex` everywhere); `true` = it must be open (no `noindex`, no `Disallow: /`, a sitemap) | `false` |
| `public_paths` | Pages that must answer 200 and are checked for title, description, `h1`… | `["/"]` |
| `protected_paths` | Private pages. An anonymous visitor must be redirected to a login or get 401/403, never 200 | `[]` |
| `login_hosts` | Text expected in the login redirect address (e.g. `cloudflareaccess.com`); a redirect elsewhere is a warning | `[]` |
| `preview_hosts` | Extra origins on which the protected paths are also tested (e.g. the hosting platform's preview address) | `[]` |
| `canonical_host` | The one host name that others (`www`, …) must redirect to | none |
| `forbidden_strings` | Text that must not appear on public pages (old name, `lorem ipsum`, `localhost`, `TODO`…), case-insensitive | `[]` |
| `max_links` | How many internal links from the home page to check | `40` |
| `timeout` | Seconds per request | `15` |

## What it does not do

- **It does not render JavaScript or use a browser.** It cannot see content added by scripts, measure layout overflow on phones or check visual quality. Use a browser-based tool for that.
- **It cannot discover preview addresses for you.** You list them in `preview_hosts`.
- **It is not a security audit or a penetration test.** It confirms a few launch conditions; passing does not mean the site is secure.
- **A redirect to a login is not proof the login is configured correctly**: it only shows that anonymous visitors do not get the content.
- It follows redirects only within the same host and stops at the first redirect to another host.

## Development

```bash
python -m unittest discover -s tests -t . -v
```

The tests use a throwaway local HTTP server; they need no network access.

## Support

Shared as-is. There is **no guaranteed support**; issues and pull requests may take a long time to be answered or may not be answered.

## License

MIT. See [LICENSE](LICENSE).
