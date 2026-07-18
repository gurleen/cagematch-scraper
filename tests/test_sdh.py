from pathlib import Path

import duckdb
from parsel import Selector

from cagematch_scraper.export import warehouse
from cagematch_scraper.spiders.sdh import titles as sdh_titles
from cagematch_scraper.spiders.sdh.titles import parse_title_page
from cagematch_scraper.spiders.sdh.wrestlers import parse_wrestler_page

FIXTURES = Path(__file__).parent / "fixtures" / "sdh"


def test_parse_wwe_championship_title() -> None:
    html = (FIXTURES / "wwe_championship.html").read_text(encoding="utf-8")
    url = "https://www.thesmackdownhotel.com/title-history/wwe/wwe-championship"
    item = parse_title_page(Selector(text=html), url)

    assert item["id"] == "wwe/wwe-championship"
    assert "Championship" in item["name"]
    assert item["profile_url"] == url
    assert item["reigns"]
    assert item["reigns"][0]["champions"]
    assert item["reigns"][0]["champions"][0]["id"] == "cm-punk"
    assert item["reigns"][0]["from_date"] == "July 6, 2026"
    assert item["reigns"][0]["to_date"] is None
    assert item["reigns"][0]["event_name"] == "Raw"
    assert item["reigns"][0]["is_vacant"] is False

    vacant = next(r for r in item["reigns"] if r.get("is_vacant"))
    assert vacant["champions"] == []
    assert vacant["from_date"] == "November 5, 2015"
    assert vacant["notes"] and "vacated" in vacant["notes"].lower()

    # Older reign ends when the newer one starts (page order is newest-first).
    assert item["reigns"][1]["to_date"] == item["reigns"][0]["from_date"]

    # Belt images: og:image is the original current design; each name-history era
    # carries the original full-size lightbox asset (not the CDN-resized variant).
    assert item["image_url"] == (
        "https://www.thesmackdownhotel.com/images/wrestling/titles/wwe/"
        "undisputed-wwe-universal-championship.png"
    )
    assert item["name_history"]
    assert all(e["image_url"] and "/images/wrestling/titles/" in e["image_url"] for e in item["name_history"])
    assert not any("jch-optimize" in (e["image_url"] or "") for e in item["name_history"])


def test_title_index_links_are_detail_pages() -> None:
    html = (FIXTURES / "wwe_titles_index.html").read_text(encoding="utf-8")
    hrefs = Selector(text=html).css('a[href*="/title-history/"]::attr(href)').getall()
    detail = [
        h
        for h in hrefs
        if (m := sdh_titles.TITLE_HREF_RE.match(h.split("?")[0])) and m.group(1) == "wwe"
    ]
    assert "wwe-championship" in {h.rstrip("/").split("/")[-1] for h in detail}
    assert len(detail) >= 10


def test_parse_cm_punk_wrestler() -> None:
    html = (FIXTURES / "cm_punk.html").read_text(encoding="utf-8")
    url = "https://www.thesmackdownhotel.com/wrestlers/cm-punk"
    item = parse_wrestler_page(Selector(text=html), url)

    assert item["id"] == "cm-punk"
    assert item["name"] == "CM Punk"
    assert item["real_name"] == "Phillip Jack Brooks"
    assert item["gender"] == "Male"
    assert item["birthday"] == "October 26, 1978"
    assert item["age"] == 47
    assert item["nationality"] and "United States" in item["nationality"]
    assert item["birthplace"] == "Chicago, Illinois"
    assert item["height_cm"] == 188
    assert item["weight_kg"] == 99
    assert any("Best In The World" in n for n in item["nicknames"])
    assert any("GTS" in f for f in item["finishers"])
    assert item["name_history"]
    assert item["name_history"][0]["name"] == "CM Punk"
    assert item["promotions"]
    assert any(p["promotion"] == "WWE" for p in item["promotions"])
    assert item["roles"]
    assert item["alignments"]
    assert any(a["alignment"] == "Face" for a in item["alignments"])

    assert item["image_url"] == (
        "https://www.thesmackdownhotel.com/images/wrestling/wrestlers/full-body/cm-punk-26.png"
    )
    assert item["images"]
    assert all(e["image_url"].startswith("https://") for e in item["images"])
    labels = [e["label"] for e in item["images"]]
    assert "Apr 2026" in labels


def test_sdh_export_flatten(tmp_path: Path) -> None:
    import json

    titles_path = tmp_path / "sdh_titles.jsonl"
    wrestlers_path = tmp_path / "sdh_wrestlers.jsonl"

    title_html = (FIXTURES / "wwe_championship.html").read_text(encoding="utf-8")
    wrestler_html = (FIXTURES / "cm_punk.html").read_text(encoding="utf-8")
    title = parse_title_page(
        Selector(text=title_html),
        "https://www.thesmackdownhotel.com/title-history/wwe/wwe-championship",
    )
    wrestler = parse_wrestler_page(
        Selector(text=wrestler_html),
        "https://www.thesmackdownhotel.com/wrestlers/cm-punk",
    )
    titles_path.write_text(json.dumps(title) + "\n", encoding="utf-8")
    wrestlers_path.write_text(json.dumps(wrestler) + "\n", encoding="utf-8")

    con = duckdb.connect(":memory:")
    warehouse.ensure_schema(con)
    warehouse.load_source(con, "sdh_titles", titles_path)
    warehouse.load_source(con, "sdh_wrestlers", wrestlers_path)

    assert con.execute("SELECT count(*) FROM sdh_titles").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM sdh_title_reigns").fetchone()[0] >= 1
    champ = con.execute(
        "SELECT wrestler_id FROM sdh_title_reign_champions WHERE wrestler_id = 'cm-punk' LIMIT 1"
    ).fetchone()
    assert champ is not None

    row = con.execute(
        "SELECT real_name, birthday, height_cm FROM sdh_wrestlers WHERE id = 'cm-punk'"
    ).fetchone()
    assert row == ("Phillip Jack Brooks", "October 26, 1978", 188)
    assert con.execute("SELECT count(*) FROM sdh_wrestler_attributes").fetchone()[0] >= 1
    assert con.execute("SELECT count(*) FROM sdh_wrestler_promotions").fetchone()[0] >= 1

    title_image = con.execute(
        "SELECT image_url FROM sdh_titles WHERE id = 'wwe/wwe-championship'"
    ).fetchone()[0]
    assert title_image and title_image.endswith(".png")
    assert (
        con.execute(
            "SELECT count(*) FROM sdh_title_name_history WHERE image_url IS NOT NULL"
        ).fetchone()[0]
        >= 1
    )
    assert con.execute("SELECT count(*) FROM sdh_wrestler_images").fetchone()[0] >= 1
    wrestler_image = con.execute(
        "SELECT image_url FROM sdh_wrestlers WHERE id = 'cm-punk'"
    ).fetchone()[0]
    assert wrestler_image and "full-body" in wrestler_image
