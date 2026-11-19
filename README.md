# Academic Lineage Tree

An interactive map of who trained whom among computer science faculty at US universities. Search a professor,
filter by research area or institution, and see their advisors (to the left) and students (to the right),
generation by generation. It is meant to help prospective PhD students understand a potential advisor's
academic background and research family.

**Live site:** https://xinyi-liu-ai.github.io/academic-lineage-tree/

## Data

| Source | What it gives | License |
|---|---|---|
| [CSRankings](https://csrankings.org) | faculty at 189 US institutions (refreshed Nov 2026): name, institution, area, homepage, Google Scholar | CC BY-NC-SA 4.0 |
| Curated sheet | advisor names collected by hand from CVs and homepages | CC BY-NC-SA 4.0 |
| [Wikidata](https://www.wikidata.org) | doctoral advisor (P184) / doctoral student (P185), matched by Google Scholar ID (P1960) or name | CC0 |
| [English Wikipedia](https://en.wikipedia.org) | `doctoral_advisor` / `doctoral_students` infobox fields | CC BY-SA 4.0 |
| Professor homepages / CVs | phrases like "PhD advisor: …" or "under the supervision of …", extracted automatically for professors not covered above (≈88% agreement with Wikidata where both exist); each links to its source page | facts only; page text is not redistributed |
| [dblp](https://dblp.org) | for professors still without an advisor: the senior co-author most often listed last on their papers during their PhD years; only confident cases kept (≈83% correct when checked against 2,177 known advisors); shown as **inferred** | CC0 |

When a curated PhD advisor disagrees with Wikidata or Wikipedia for the same person, the curated one is kept but
marked **unverified** on the site. Faculty who are no longer on CSRankings' list for these institutions (moved, retired, emeritus) are kept for their lineage and marked **former**. Data can be wrong or incomplete; always confirm with the professor's own CV.

## Rebuilding the data

```bash
pip install requests pypdf
python scripts/update_faculty.py --download   # refresh data/raw/professors.csv from the latest CSRankings
python scripts/enrich_wikidata.py          # writes data/enrich/wikidata.jsonl + data/cache/wikidata_match.json
python scripts/match_wikidata_employer.py  # same-institution matching -> data/enrich/wikidata_employer_match.json
python scripts/enrich_wikidata.py          # rerun to pick up the institution matches
python scripts/enrich_wikipedia.py         # writes data/enrich/wikipedia.jsonl
python scripts/fetch_pages.py              # ~45 min, polite crawl of homepages/CVs into data/cache/pages (not committed)
python scripts/extract_advisors.py         # writes data/enrich/homepage.jsonl (add --eval to measure precision)
curl -o data/cache/dblp/dblp.xml.gz https://dblp.org/xml/dblp.xml.gz   # ~1.1 GB, CC0
python scripts/infer_dblp.py --eval        # writes data/enrich/dblp.jsonl (co-authorship inference)
python scripts/build_graph.py              # writes docs/data/graph.json
python scripts/fetch_people_pages.py       # ~1 h, polite crawl of professors' pages + Students/People/Alumni subpages
python scripts/extract_students.py         # student lists from those pages -> data/enrich/students_homepage.jsonl
python scripts/build_graph.py
python scripts/enrich_people.py            # homepages / LinkedIn / current positions for people outside the list
python scripts/match_people_links.py       # personal homepage links found on advisors' pages
python scripts/build_graph.py              # final graph
```

To preview locally: `python -m http.server 8765 --directory docs` and open http://localhost:8765.

## Layout

- `data/raw/professors.csv` – faculty list with curated advisor columns and a `status` column (current / moved / new / former)
- `data/raw/universities.csv` – institution names and short names
- `data/raw/manual_students.csv` – advisor/student links added by hand (e.g. reported by the student); add rows here or open an issue
- `data/enrich/` – relations pulled from Wikidata and Wikipedia
- `scripts/` – matching, cleaning and graph-building scripts
- `docs/` – the static site served by GitHub Pages (vanilla JS, no build step); layout follows the team's Figma mock-up

## License

Code: MIT. Data in `data/` and `docs/data/`: CC BY-NC-SA 4.0 (inherits CSRankings' license), with Wikidata content
under CC0 and Wikipedia content under CC BY-SA 4.0.
