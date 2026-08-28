# arxiv2epub

Turn an arXiv link into a well-formatted EPUB you can send to a Kindle.

```console
$ arxiv2epub https://arxiv.org/abs/1706.03762
out/attention-is-all-you-need-1706.03762v7.epub  (0.7 MiB)
```

## What it produces

A validating EPUB 3 (checked with [epubcheck](https://github.com/w3c/epubcheck),
0 errors on the sample papers) with an EPUB 2 NCX fallback, because Amazon's
Send-to-Kindle converter still reads it.

- **One file per section**, with a nested table of contents down to subsections,
  so the Kindle's own navigation and progress bar behave.
- **Equations as images**, sized in `em` and given a baseline offset, so inline
  maths sits on the text line and scales when the reader changes font size.
  Kindle's MathML support is unreliable; pictures are not.
- **Figures pulled local**, downscaled to e-ink resolution, and re-encoded —
  PNG for line art, JPEG for photographs.
- **Real popup footnotes** (`epub:type="noteref"`/`"footnote"`) rather than the
  mid-sentence asides LaTeXML emits.
- **Working cross-references**: citations, figure numbers, and section links
  keep working after the paper is split across files.
- **Prose rescued from TikZ frames.** LaTeXML puts the text of a framed block
  inside `<foreignObject>`, which e-readers render as an empty grey box; where
  the drawing is only the frame, the text is lifted out instead.
- **A generated cover**, so a shelf of converted papers is browsable.
- Full metadata — every author, the abstract as the description, arXiv
  categories as subjects, the DOI, and the exact version converted.

## How it works

arXiv publishes LaTeXML-rendered HTML for most papers at `/html/<id>v<n>`;
[ar5iv](https://ar5iv.labs.arxiv.org) covers the older backlog with the same
pipeline and the same `ltx_*` markup. That HTML is already semantic — sections
are `<section>`, equations are MathML, figures have real captions — so the
conversion is a rewrite rather than an extraction, and nothing is inferred from
PDF layout.

```
arXiv id  ->  Atom API (metadata, latest version)
          ->  arxiv.org/html  ->  ar5iv          (whichever answers first)
          ->  transform: strip chrome, flatten equation tables, draw maths,
                         fetch figures, rewrite footnotes and links, split
          ->  EPUB 3 + NCX
```

Maths is drawn by [ziamath](https://ziamath.readthedocs.io), which renders
MathML using glyph outlines from STIX Two Math. The resulting SVG carries no
font dependency, so it looks the same on every device.

## The mail-in worker

The other half of this is a service you leave running: it watches a mailbox for
arXiv links, converts whatever arrives, and mails the EPUB to your Kindle.

```console
cp .env.example .env    # then fill it in
docker compose up -d worker
docker compose logs -f worker
```

Send that mailbox an email with an arXiv link in the subject or the body and
the paper turns up on your Kindle. You get a reply either way — what was sent,
that you had already sent it, or why it could not be converted.

**Several links in one email is fine.** Each paper is converted and delivered
separately, in the order you wrote them, and you get a single reply covering
the lot rather than one per paper. A paper named twice is converted once, and
one failure does not stop the others. A message is capped at
`MAX_LINKS_PER_EMAIL` papers (10 by default) so a forwarded digest cannot flood
your Kindle; anything over the cap is listed in the reply rather than dropped
silently.

It reads the **same `.env` as the previous arxiv2kindle worker**, variable for
variable. `CHROME_PATH` is accepted and ignored, since this version renders
from arXiv's HTML instead of driving a headless browser; leaving it in place
does no harm.

| Variable | Meaning |
| --- | --- |
| `EMAIL_USER` / `EMAIL_PASSWORD` | The mailbox to watch, and to send from. |
| `EMAIL_IMAP_HOST` / `EMAIL_IMAP_PORT` | Inbox, over TLS. Default port 993. |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | Outgoing. 465 is implicit TLS, anything else uses STARTTLS. |
| `KINDLE_EMAIL` | Where the EPUB is delivered. |
| `ALLOWED_SENDERS` | Comma-separated. Anything else is left untouched. |
| `POLL_INTERVAL_MS` | Inbox check interval, in milliseconds. |
| `CHROME_PATH` | Ignored; kept so an inherited `.env` still validates. |

Optional, all with working defaults: `MATH_FORMAT`, `MAX_ATTACHMENT_MB`,
`MAX_LINKS_PER_EMAIL`, `MAILBOX`, `OUTPUT_DIR`, `CACHE_DIR`, `DRY_RUN`,
`SMTP_ALLOW_CLEARTEXT`, `TZ`.

Replies are threaded onto the message that asked for them (`In-Reply-To`,
`References`, `Auto-Submitted`), which is what keeps them out of spam — an
unsolicited note from a young domain gets filed, a reply to your own thread
does not. If one does land in spam, mark it "not spam" once; that teaches the
filter far faster than any header will.

Check the configuration without touching the network, or drain the inbox once
and exit:

```console
docker compose run --rm worker --check
docker compose run --rm worker --once
```

**Amazon has to be told to accept the mail.** Add `EMAIL_USER` to your Approved
Personal Document E-mail List under *Manage Your Content and Devices → Preferences
→ Personal Document Settings*, or Amazon silently drops everything the worker sends.

### How it behaves when things go wrong

It runs unattended, so the failure paths are the design:

- **Nothing is marked read until it has been answered.** Mail is fetched with
  `BODY.PEEK[]`, so a crash mid-conversion leaves the request to be retried
  rather than swallowing it — the failure mode the previous worker had, which
  marked everything read at fetch time.
- **Mail from an unknown sender is left unread**, not consumed, so you still
  see it. It is logged once per run rather than on every pass.
- **Every handled message gets a reply**, including failures. A paper neither
  provider can convert produces an explanation, not silence.
- **A duplicate is recognised** by the arXiv id already present in the output
  folder, and answered without sending a second copy.
- **Oversized books are not mailed.** The file stays on the server and the
  reply says where.
- **A password is never sent over an unencrypted connection.** If the SMTP
  server offers no STARTTLS the worker refuses and says so; set
  `SMTP_ALLOW_CLEARTEXT=true` only for a relay on your own network.
- An unreachable mail server is logged and retried on the next pass; one bad
  message never blocks the next.

## Usage

```console
arxiv2epub <reference> [<reference> ...] [-o OUTPUT]
```

A reference is anything an arXiv id can be read out of: `1706.03762`,
`arXiv:2501.12948v1`, an `abs`/`pdf`/`html` URL, an ar5iv URL, or a
`10.48550/arXiv.*` DOI. Give `-o` a directory and the filename is derived from
the paper; give it a path ending in `.epub` and that exact file is written.

| Option | Effect |
| --- | --- |
| `--math svg` | One SVG file per equation, deduplicated (default). |
| `--math inline-svg` | SVG inlined into the text so equations follow the reader's text colour — better in dark mode, larger files. |
| `--math png` | Raster equations, for readers that will not draw SVG. |
| `--source arxiv\|ar5iv` | Force one HTML provider instead of taking whichever answers first. |
| `--cache DIR` | Reuse downloaded pages and figures between runs. |
| `--no-images` | Skip figures entirely. |
| `--no-cover` | Skip the generated cover. |
| `-v` / `-q` | More or less logging. |

Pinning a version (`2501.12948v1`) converts that version; otherwise the newest
one is used and recorded in the filename and metadata.

## Running it

### Docker

`docker compose up -d` starts the worker and nothing else; the one-shot CLI
services sit behind a `tools` profile.

```console
docker compose build
docker compose run --rm cli --cache /cache -o /out 1706.03762
```

Output lands in `./out`, downloads are cached in `./cache`. The `samples`
service rebuilds the three papers used during development:

```console
docker compose run --rm samples
```

### Locally

```console
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/arxiv2epub 1706.03762 -o out/
```

Requires Python 3.10+. `--math png` additionally needs libcairo; the Docker
image installs it, along with the DejaVu serif faces the cover is drawn with.

## Tests

```console
.venv/bin/python -m pytest
```

The suite is offline — network responses are stubbed — so it runs anywhere.

## As a library

```python
from arxiv2epub import build_epub
from arxiv2epub.pipeline import Options

result = build_epub("1706.03762", "out/", Options(math_format="inline-svg"))
print(result.path, result.warnings)
```

`build_epub` is the seam the mail-in worker sits on: it takes a reference,
writes a file, and reports what went wrong without raising for recoverable
problems like one missing figure.

## Limitations

- Papers with no HTML rendering on either arXiv or ar5iv cannot be converted;
  there is no PDF fallback, because reflowing two-column PDF text produces a
  worse book than not producing one. This mostly affects pre-2000 submissions
  whose original TeX defeats LaTeXML — `hep-th/9711200`, for instance.
- Very wide tables are shrunk but not restructured, and can still be cramped on
  a small screen.
- External SVG equations render in the reader's image layer, so on a device in
  dark mode they stay black. Use `--math inline-svg` if you read that way.
