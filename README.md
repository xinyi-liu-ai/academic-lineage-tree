# Academic Lineage Tree

An interactive map of who trained whom among computer science faculty at US universities. Search a professor,
filter by research area or institution, and see their advisors (to the left) and students (to the right),
generation by generation. It is meant to help prospective PhD students understand a potential advisor's
academic background and research family.

**Live site:** https://xinyi-liu-ai.github.io/academic-lineage-tree/

## Data

| Source | What it gives | License |
|---|---|---|
| [CSRankings](https://csrankings.org) | 5,080 faculty at 189 institutions: name, institution, area, homepage, Google Scholar | CC BY-NC-SA 4.0 |
| Curated sheet | advisor names collected by hand from CVs and homepages | CC BY-NC-SA 4.0 |
| [Wikidata](https://www.wikidata.org) | doctoral advisor (P184) / doctoral student (P185), matched by Google Scholar ID (P1960) or name | CC0 |
| [English Wikipedia](https://en.wikipedia.org) | `doctoral_advisor` / `doctoral_students` infobox fields | CC BY-SA 4.0 |
| Professor homepages / CVs | phrases like "PhD advisor: …" or "under the supervision of …", extracted automatically for professors not covered above (≈88% agreement with Wikidata where both exist); each links to its source page | facts only; page text is not redistributed |

When a curated PhD advisor disagrees with Wikidata or Wikipedia for the same person, the curated one is kept but
marked **unverified** on the site. Data can be wrong or incomplete; always confirm with the professor's own CV.

## Rebuilding the data

```bash
pip install requests
python scripts/enrich_wikidata.py          # writes data/enrich/wikidata.jsonl + data/cache/wikidata_match.json
python scripts/match_wikidata_employer.py  # same-institution matching -> data/enrich/wikidata_employer_match.json
python scripts/enrich_wikidata.py          # rerun to pick up the institution matches
python scripts/enrich_wikipedia.py         # writes data/enrich/wikipedia.jsonl
python scripts/fetch_pages.py              # ~45 min, polite crawl of homepages/CVs into data/cache/pages (not committed)
python scripts/extract_advisors.py         # writes data/enrich/homepage.jsonl (add --eval to measure precision)
python scripts/build_graph.py              # writes docs/data/graph.json
```

To preview locally: `python -m http.server 8765 --directory docs` and open http://localhost:8765.

## Layout

- `data/raw/professors.csv` – faculty list with curated advisor columns
- `data/raw/universities.csv` – institution names and short names
- `data/enrich/` – relations pulled from Wikidata and Wikipedia
- `scripts/` – matching, cleaning and graph-building scripts
- `docs/` – the static site served by GitHub Pages (vanilla JS + D3)

## License

Code: MIT. Data in `data/` and `docs/data/`: CC BY-NC-SA 4.0 (inherits CSRankings' license), with Wikidata content
under CC0 and Wikipedia content under CC BY-SA 4.0.
