import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

USER = "nfx"
SITE = "https://nfx.github.io/"
TITLE = "Serge Smertin OSS"
SUMMARY = "OSS by Serge Smertin (github.com/nfx)."
OSSINSIGHT = "https://ossinsight.io/analyze-user/nfx"
GOOGLE_VERIFICATION = "googleb777978f5734d9ea"
EXCLUDE = set()
STATIC = ["index.html", "avatar.png", "robots.txt"]
SM = "http://www.sitemaps.org/schemas/sitemap/0.9"
TIMEOUT = 20
RETRIES = 2
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,5}) ")


class FetchError(Exception):
    pass


def fetch(url, headers=None):
    """Return the body of url, or None on 404. Raise FetchError otherwise."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "nfx.github.io-build", **(headers or {})}
    )
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
                return res.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code < 500 or attempt == RETRIES:
                raise FetchError(f"{url}: HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == RETRIES:
                raise FetchError(f"{url}: {e}") from e
        time.sleep(2**attempt)


def discover(exclude):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    names, page = [], 1
    while True:
        body = fetch(
            f"https://api.github.com/users/{USER}/repos?type=owner&per_page=100&page={page}",
            headers,
        )
        if body is None:
            raise FetchError(f"user {USER} not found")
        repos = json.loads(body)
        for r in repos:
            if not r.get("has_pages") or r.get("fork") or r.get("archived"):
                continue
            if r["name"] == f"{USER}.github.io" or r["name"] in exclude:
                continue
            names.append(r["name"])
        if len(repos) < 100:
            return sorted(names)
        page += 1


def sitemap_urls(base, url, depth=0):
    """Return [(loc, lastmod)] from a sitemap or sitemap index under base."""
    body = fetch(url)
    if body is None:
        return []
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise FetchError(f"{url}: {e}") from e
    if root.tag == f"{{{SM}}}sitemapindex":
        if depth > 0:
            raise FetchError(f"{url}: nested sitemap index")
        urls = []
        for loc in root.iterfind(f"{{{SM}}}sitemap/{{{SM}}}loc"):
            child = (loc.text or "").strip()
            if child.startswith(base):
                urls += sitemap_urls(base, child, depth + 1)
        return urls
    if root.tag != f"{{{SM}}}urlset":
        raise FetchError(f"{url}: unexpected root element {root.tag}")
    urls = []
    for u in root.iterfind(f"{{{SM}}}url"):
        loc = (u.findtext(f"{{{SM}}}loc") or "").strip()
        if not loc.startswith(base):
            continue
        urls.append((loc, (u.findtext(f"{{{SM}}}lastmod") or "").strip()))
    return urls


def write_sitemap(path, urls):
    ET.register_namespace("", SM)
    urlset = ET.Element(f"{{{SM}}}urlset")
    for loc, lastmod in urls:
        u = ET.SubElement(urlset, f"{{{SM}}}url")
        ET.SubElement(u, f"{{{SM}}}loc").text = loc
        if lastmod:
            ET.SubElement(u, f"{{{SM}}}lastmod").text = lastmod
    ET.ElementTree(urlset).write(path, encoding="utf-8", xml_declaration=True)


def demote(name, text):
    """Nest a project's llms.txt under an H2 section named after the project."""
    out, in_fence, seen_h1, seen_h2 = [], False, False, False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = HEADING.match(line)
            if m:
                level = len(m.group(1))
                seen_h1 = seen_h1 or level == 1
                seen_h2 = seen_h2 or level == 2
                line = "#" + line
            elif not seen_h2 and line.startswith(">"):
                # The summary blockquote becomes a plain paragraph.
                line = line.lstrip(">").lstrip()
        out.append(line)
    if not seen_h1:
        out = [f"## {name}", ""] + out
    return "\n".join(out).strip() + "\n"


def head_lastmod():
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def build(out, exclude):
    here = os.path.dirname(os.path.abspath(__file__))
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    for name in STATIC:
        shutil.copy2(os.path.join(here, name), os.path.join(out, name))
    with open(
        os.path.join(out, f"{GOOGLE_VERIFICATION}.html"), "w", encoding="utf-8"
    ) as f:
        f.write(f"google-site-verification: {GOOGLE_VERIFICATION}.html")

    projects = discover(exclude)
    print(f"projects: {', '.join(projects) or '(none)'}")

    urls = [(SITE, head_lastmod())]
    llms = [
        (
            f"# {TITLE}\n\n> {SUMMARY}\n\n"
            f"Full documentation for all projects in a single file: {SITE}llms-full.txt\n\n"
            f"- [OSS Insight]({OSSINSIGHT}): contribution analytics for github.com/{USER}\n"
        )
    ]
    full = [f"# {TITLE}\n\n> {SUMMARY}\n"]
    for name in projects:
        base = f"{SITE}{name}/"
        found = sitemap_urls(base, f"{base}sitemap.xml")
        urls += found
        txt = fetch(f"{base}llms.txt")
        if txt is not None:
            llms.append(demote(name, txt.decode("utf-8")))
        txt_full = fetch(f"{base}llms-full.txt")
        if txt_full is not None:
            body = txt_full.decode("utf-8").strip()
            heading = f"# {name}"
            if body.startswith("# "):
                heading, _, body = body.partition("\n")
            full.append(f"{heading}\n\nSource: {base}llms-full.txt\n\n{body.strip()}\n")
        print(
            f"  {name}: {len(found)} urls, llms.txt={txt is not None}, llms-full.txt={txt_full is not None}"
        )

    write_sitemap(os.path.join(out, "sitemap.xml"), urls)
    with open(os.path.join(out, "llms.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(llms))
    with open(os.path.join(out, "llms-full.txt"), "w", encoding="utf-8") as f:
        f.write("\n---\n\n".join(full))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="site", help="output directory")
    parser.add_argument(
        "--exclude", action="append", default=[], help="repository to skip (repeatable)"
    )
    args = parser.parse_args()
    try:
        build(args.out, EXCLUDE | set(args.exclude))
    except FetchError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
