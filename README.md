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
| `--cache DIR` | Reuse downloaded pages and figures between runs. |
| `--no-images` | Skip figures entirely. |
| `--no-cover` | Skip the generated cover. |
| `-v` / `-q` | More or less logging. |

Pinning a version (`2501.12948v1`) converts that version; otherwise the newest
one is used and recorded in the filename and metadata.

## Running it

### Docker

```console
docker compose build
docker compose run --rm arxiv2epub --cache /cache -o /out 1706.03762
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

`build_epub` is the seam the planned mail-in/send-to-Kindle service will sit on:
it takes a reference, writes a file, and reports what went wrong without
raising for recoverable problems like one missing figure.

## Limitations

- Papers with no HTML rendering on either arXiv or ar5iv cannot be converted;
  there is no PDF fallback, because reflowing two-column PDF text produces a
  worse book than not producing one.
- Very wide tables are shrunk but not restructured, and can still be cramped on
  a small screen.
- External SVG equations render in the reader's image layer, so on a device in
  dark mode they stay black. Use `--math inline-svg` if you read that way.
